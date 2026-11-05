"""
AI-Powered Multilingual Shopping Intelligence Platform
Spending Mandate & Deterministic Policy Engine Integration Tests.
"""

import pytest
import httpx
from fastapi import status


@pytest.mark.asyncio
async def test_create_mandate_success(async_client: httpx.AsyncClient):
    """Verify mandate creation establishes guest session and persists policy rules."""
    payload = {
        "total_budget": 1000.0,
        "maximum_item_price": 500.0,
        "maximum_quantity": 2,
        "currency": "USD",
        "session_identifier": "guest_test_session_001",
    }
    response = await async_client.post("/api/mandates", json=payload)
    assert response.status_code == status.HTTP_201_CREATED

    data = response.json()
    assert "id" in data
    assert "session_id" in data
    assert data["total_budget"] == 1000.0
    assert data["maximum_item_price"] == 500.0
    assert data["maximum_quantity"] == 2
    assert data["currency"] == "USD"


@pytest.mark.asyncio
async def test_create_mandate_invalid_ceilings(async_client: httpx.AsyncClient):
    """Verify error when maximum_item_price exceeds total_budget."""
    payload = {
        "total_budget": 300.0,
        "maximum_item_price": 500.0,
        "maximum_quantity": 1,
        "currency": "USD",
    }
    response = await async_client.post("/api/mandates", json=payload)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "maximum_item_price cannot exceed total_budget" in data["detail"]


@pytest.mark.asyncio
async def test_get_mandate_success(async_client: httpx.AsyncClient):
    """Verify retrieval of an existing session's spending mandate."""
    # First create
    create_resp = await async_client.post(
        "/api/mandates",
        json={
            "total_budget": 800.0,
            "maximum_item_price": 400.0,
            "maximum_quantity": 2,
            "currency": "USD",
            "session_identifier": "guest_retrieval_session",
        },
    )
    session_id = create_resp.json()["session_id"]

    # Now get
    get_resp = await async_client.get(f"/api/mandates/{session_id}")
    assert get_resp.status_code == status.HTTP_200_OK
    assert get_resp.json()["session_id"] == session_id
    assert get_resp.json()["total_budget"] == 800.0


@pytest.mark.asyncio
async def test_get_mandate_not_found(async_client: httpx.AsyncClient):
    """Verify 404 Problem Details when retrieving mandate for unknown session."""
    response = await async_client.get("/api/mandates/non_existent_session_id")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert "No spending mandate found" in data["detail"]


@pytest.mark.asyncio
async def test_update_mandate_success(async_client: httpx.AsyncClient):
    """Verify updating mandate limits."""
    # Create
    create_resp = await async_client.post(
        "/api/mandates",
        json={
            "total_budget": 600.0,
            "maximum_item_price": 300.0,
            "maximum_quantity": 1,
            "currency": "USD",
        },
    )
    session_id = create_resp.json()["session_id"]

    # Update budget to 750
    update_resp = await async_client.put(
        f"/api/mandates/{session_id}",
        json={"total_budget": 750.0, "maximum_quantity": 3},
    )
    assert update_resp.status_code == status.HTTP_200_OK
    assert update_resp.json()["total_budget"] == 750.0
    assert update_resp.json()["maximum_quantity"] == 3
    # Preserves unchanged field
    assert update_resp.json()["maximum_item_price"] == 300.0


@pytest.mark.asyncio
async def test_policy_evaluation_within_budget_allowed(async_client: httpx.AsyncClient):
    """
    Test Policy Rule: Candidate purchase fully within budget, item price, and quantity
    must result in ALLOWED.
    """
    # Setup mandate: Budget $1000, Max item $500, Max Qty 2
    create_resp = await async_client.post(
        "/api/mandates",
        json={
            "total_budget": 1000.0,
            "maximum_item_price": 500.0,
            "maximum_quantity": 2,
            "currency": "USD",
        },
    )
    session_id = create_resp.json()["session_id"]

    # Candidate purchase: 1 unit of $348.00 Sony WH-1000XM5
    eval_resp = await async_client.post(
        f"/api/mandates/{session_id}/validate",
        json={
            "item_name": "Sony WH-1000XM5",
            "unit_price": 348.0,
            "quantity": 1,
            "currency": "USD",
        },
    )
    assert eval_resp.status_code == status.HTTP_200_OK
    result = eval_resp.json()
    assert result["decision"] == "ALLOWED"
    assert result["is_allowed"] is True
    assert result["purchase_amount"] == 348.0
    assert result["remaining_budget_after_purchase"] == 652.0


@pytest.mark.asyncio
async def test_policy_evaluation_exceeds_total_budget_blocked(async_client: httpx.AsyncClient):
    """
    Test Policy Rule: Candidate purchase where total cost exceeds budget
    must result in BLOCKED with human-readable reason.
    """
    # Setup mandate: Budget $500, Max item $500, Max Qty 2
    create_resp = await async_client.post(
        "/api/mandates",
        json={
            "total_budget": 500.0,
            "maximum_item_price": 500.0,
            "maximum_quantity": 2,
            "currency": "USD",
        },
    )
    session_id = create_resp.json()["session_id"]

    # Candidate purchase: 2 units at $300 = $600 (exceeds $500 budget)
    eval_resp = await async_client.post(
        f"/api/mandates/{session_id}/validate",
        json={
            "item_name": "Two Devices",
            "unit_price": 300.0,
            "quantity": 2,
            "currency": "USD",
        },
    )
    assert eval_resp.status_code == status.HTTP_200_OK
    result = eval_resp.json()
    assert result["decision"] == "WARNING_OVERRIDE_REQUIRED"
    assert result["is_allowed"] is False
    assert result["is_budget_exceeded"] is True
    assert result["is_override_eligible"] is True
    assert result["budget_exceeded_amount"] == 100.0
    assert any("exceeds your remaining mandate budget" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_policy_evaluation_exceeds_max_item_price_blocked(async_client: httpx.AsyncClient):
    """
    Test Policy Rule: Candidate purchase where item unit price exceeds max_item_price
    must result in BLOCKED even if total budget is sufficient.
    """
    # Setup mandate: Budget $2000, Max item $400, Max Qty 5
    create_resp = await async_client.post(
        "/api/mandates",
        json={
            "total_budget": 2000.0,
            "maximum_item_price": 400.0,
            "maximum_quantity": 5,
            "currency": "USD",
        },
    )
    session_id = create_resp.json()["session_id"]

    # Candidate purchase: iPhone 15 Pro at $989.00 (exceeds $400 max item ceiling)
    eval_resp = await async_client.post(
        f"/api/mandates/{session_id}/validate",
        json={
            "item_name": "Apple iPhone 15 Pro",
            "unit_price": 989.0,
            "quantity": 1,
            "currency": "USD",
        },
    )
    assert eval_resp.status_code == status.HTTP_200_OK
    result = eval_resp.json()
    assert result["decision"] == "WARNING_OVERRIDE_REQUIRED"
    assert result["is_allowed"] is False
    assert result["is_budget_exceeded"] is True
    assert result["is_override_eligible"] is True
    assert result["budget_exceeded_amount"] == 589.0
    assert any("exceeds your maximum permitted item price limit" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_policy_evaluation_exceeds_max_quantity_blocked(async_client: httpx.AsyncClient):
    """
    Test Policy Rule: Candidate purchase where quantity exceeds maximum_quantity
    must result in BLOCKED.
    """
    # Setup mandate: Budget $2000, Max item $500, Max Qty 2
    create_resp = await async_client.post(
        "/api/mandates",
        json={
            "total_budget": 2000.0,
            "maximum_item_price": 500.0,
            "maximum_quantity": 2,
            "currency": "USD",
        },
    )
    session_id = create_resp.json()["session_id"]

    # Candidate purchase: 4 units of $50 item
    eval_resp = await async_client.post(
        f"/api/mandates/{session_id}/validate",
        json={
            "item_name": "Accessories",
            "unit_price": 50.0,
            "quantity": 4,
            "currency": "USD",
        },
    )
    assert eval_resp.status_code == status.HTTP_200_OK
    result = eval_resp.json()
    assert result["decision"] == "BLOCKED"
    assert result["is_allowed"] is False
    assert any("exceeds your maximum permitted quantity limit" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_policy_evaluation_missing_mandate_blocked(async_client: httpx.AsyncClient):
    """
    Test Policy Rule: Candidate purchase for a session with no configured mandate
    must result in BLOCKED.
    """
    eval_resp = await async_client.post(
        "/api/mandates/sess_unknown_empty/validate",
        json={
            "item_name": "Random Product",
            "unit_price": 100.0,
            "quantity": 1,
            "currency": "USD",
        },
    )
    assert eval_resp.status_code == status.HTTP_200_OK
    result = eval_resp.json()
    assert result["decision"] == "BLOCKED"
    assert result["is_allowed"] is False
    assert any("No active spending mandate found" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_policy_evaluation_currency_mismatch_blocked(async_client: httpx.AsyncClient):
    """
    Test Policy Rule: Currency mismatch (e.g. attempting EUR purchase against USD mandate)
    must result in BLOCKED.
    """
    create_resp = await async_client.post(
        "/api/mandates",
        json={
            "total_budget": 1000.0,
            "maximum_item_price": 500.0,
            "maximum_quantity": 2,
            "currency": "USD",
        },
    )
    session_id = create_resp.json()["session_id"]

    eval_resp = await async_client.post(
        f"/api/mandates/{session_id}/validate",
        json={
            "item_name": "European Import",
            "unit_price": 300.0,
            "quantity": 1,
            "currency": "EUR",
        },
    )
    assert eval_resp.status_code == status.HTTP_200_OK
    result = eval_resp.json()
    assert result["decision"] == "BLOCKED"
    assert result["is_allowed"] is False
    assert any("does not match your active mandate currency" in r for r in result["reasons"])
