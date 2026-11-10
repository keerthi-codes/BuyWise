# BUYWISE AI – AI-Powered Shopping Intelligence Platform

> **Discover smarter. Compare deeper. Spend responsibly.**

An intelligent shopping aggregation and spending-control platform developed for the **PayPal AI Hackathon**.

BUYWISE AI combines live multi-retailer product aggregation, AI-assisted shopping recommendations, transparent product comparisons, and deterministic spending mandates to help users make smarter and more responsible purchasing decisions.

Unlike conventional shopping platforms that focus primarily on product discovery, BUYWISE AI introduces an additional layer of financial control by ensuring that purchasing decisions are validated against user-defined spending limits.

---

## 1. Project Overview

Online shopping offers access to thousands of products across multiple retailers. However, comparing products, identifying competitive prices, and maintaining control over spending can be challenging.

BUYWISE AI addresses these challenges through a unified shopping intelligence platform that enables users to:

- Discover products using live multi-retailer search.
- Compare product specifications and merchant pricing.
- Receive AI-assisted shopping recommendations.
- Establish personalized spending mandates.
- Validate purchasing decisions against predefined financial constraints.
- Monitor product prices and make informed purchasing decisions.
- Complete checkout through PayPal Sandbox integration.

The platform is designed around a fundamental principle:

**AI recommends. The backend validates. The user approves. PayPal processes.**

---

## 2. Key Features

### 2.1 Live Product Aggregation
- Integrates with the Channel3 REST API for live product discovery.
- Retrieves product information and competing merchant offers.
- Supports keyword-based product searches.
- Aggregates product information from multiple retailers.
- Normalizes product information and pricing for consistent presentation.
- Preserves product provenance and provider information.

### 2.2 AI-Powered Shopping Assistant
- Enables users to interact with the platform using natural-language shopping queries.
- Provides AI-assisted product recommendations and shopping insights.
- Integrates Google Gemini for shopping intelligence.
- Connects product discovery with live Channel3 search.
- Includes a deterministic fallback mechanism to maintain basic functionality when AI services are unavailable.

### 2.3 Multi-Retailer Comparison Matrix
- Provides side-by-side product comparisons.
- Compares technical specifications and product attributes.
- Displays pricing and available merchant offers.
- Helps users evaluate competing products before purchasing.
- Supports comparison of up to four products simultaneously.

### 2.4 Deterministic Spending Mandates
- Allows users to configure personal spending restrictions.
- Enforces overall budget ceilings.
- Supports maximum item price restrictions.
- Applies quantity limits.
- Validates purchasing decisions through backend business logic.
- Prevents AI-generated recommendations from bypassing configured spending policies.

**Key innovation:** Spending restrictions are enforced independently by the backend rather than relying on AI-generated responses.

### 2.5 Price Tracking
- Supports product price monitoring.
- Maintains historical price snapshots.
- Helps users observe price changes over time.
- Supports more informed purchasing decisions.

### 2.6 PayPal Checkout Integration
- Integrates PayPal Checkout into the shopping workflow.
- Supports PayPal Sandbox for testing payment operations.
- Connects purchase validation with the checkout process.
- Separates payment processing from AI recommendation logic.

### 2.7 User Authentication and Guest Access
- Supports user registration and authentication.
- Enables guest access for product exploration.
- Maintains shopping session information.
- Supports account-based shopping functionality.

### 2.8 Shopping Analytics
- Provides insights into shopping activity.
- Supports spending-related analytics.
- Helps users understand their purchasing behavior.

### 2.9 Standardized API Error Handling
- Implements structured API error responses based on **RFC 9457 Problem Details**.
- Provides consistent error information across API endpoints.
- Improves API reliability and error transparency.

---

## 3. System Architecture

BUYWISE AI follows a frontend–backend architecture with external API integrations and persistent database storage.

### High-Level Architecture

```text
                    USER
                      |
                      v
             BUYWISE AI PLATFORM
                      |
              +-------+-------+
              |               |
              v               v
          FRONTEND         BACKEND
           Vercel           FastAPI
                              |
              +---------------+----------------+
              |               |                |
              v               v                v
        PRODUCT SERVICES  AI ASSISTANT    SPENDING ENGINE
              |               |                |
              v               v                v
           Channel3         Gemini       Policy Validation
              |
              v
       LIVE PRODUCT DATA

                      |
                      v
              CHECKOUT SERVICE
                      |
                      v
                PayPal Sandbox

                      |
                      v
               DATABASE LAYER
                      |
                      v
              Neon PostgreSQL
                (Production)
```

### Architecture Components

| Component | Responsibility |
|---|---|
| Frontend | Product discovery, comparison, shopping assistant, and user interactions |
| FastAPI Backend | API handling, business logic, validation, and service integration |
| Channel3 | Live product aggregation and merchant offer retrieval |
| Google Gemini | AI-assisted shopping recommendations and interactions |
| Spending Policy Engine | Backend enforcement of user-defined spending restrictions |
| PayPal | Checkout and payment workflow through Sandbox |
| Database | Persistent storage of application and shopping data |

---

## 4. Technology Stack

| Layer | Technology |
|---|---|
| Backend Language | Python 3.13 |
| Backend Framework | FastAPI |
| ORM | SQLAlchemy 2.0 |
| Data Validation | Pydantic v2 |
| Local Database | SQLite |
| SQLite Configuration | Write-Ahead Logging (WAL) |
| Production Database | PostgreSQL |
| Database Migrations | Alembic |
| Product Aggregation | Channel3 REST API |
| AI Integration | Google Gemini |
| Payment Integration | PayPal Sandbox |
| Frontend | HTML, CSS, JavaScript |
| Frontend Hosting | Vercel |
| Backend Hosting | Render |
| Database Hosting | Neon |
| Testing Framework | Pytest |
| Asynchronous Testing | pytest-asyncio |
| API Testing | HTTPX |

---

## 5. Project Structure
```text
BuyWise/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── schemas/
│   │   └── services/
│   ├── docs/
│   └── tests/
│
├── frontend/
│   ├── index.html
│   └── assets/
│
├── alembic/
├── docs/
├── .env.example
├── .gitignore
├── alembic.ini
├── requirements.txt
├── pytest.ini
└── README.md
```
### Backend Organization

- **API:** Handles incoming HTTP requests and exposes application endpoints.
- **Core:** Manages application configuration, database initialization, error handling, and seeding.
- **Models:** Defines SQLAlchemy domain entities and database relationships.
- **Repositories:** Encapsulates database persistence and query operations.
- **Schemas:** Defines request and response structures using Pydantic.
- **Services:** Contains business logic, Channel3 integration, and spending policy enforcement.
- **Tests:** Contains automated unit and integration tests.

---

## 6. Getting Started

Follow the instructions below to run BUYWISE AI locally.

### Prerequisites

Ensure the following are installed:

- Python 3.13
- Git
- pip
- A Channel3 API key for live product search
- A Google Gemini API key for AI-assisted features
- PayPal Sandbox credentials for checkout testing

### Step 1: Clone the Repository

```bash
git clone https://github.com/keerthi-codes/BuyWise.git

cd BuyWise
```

### Step 2: Create a Virtual Environment

**Windows PowerShell:**

```powershell
python -m venv .venv

.venv\Scripts\activate
```

### Step 3: Install Dependencies

```powershell
python -m pip install -r requirements.txt
```

### Step 4: Configure Environment Variables

Create a `.env` file using the provided `.env.example` template.

```powershell
Copy-Item .env.example .env
```

Configure the required API credentials and application settings.

Example configuration:

```env
ENVIRONMENT=development
DEBUG=true
DATABASE_URL=sqlite:///./shopping_platform.db
CHANNEL3_API_KEY=your_channel3_api_key
GEMINI_API_KEY=your_gemini_api_key

PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=your_paypal_client_id
PAYPAL_CLIENT_SECRET=your_paypal_client_secret

SECRET_KEY=your_secure_secret_key
```

Configure the database connection according to the selected environment.

- Local development: SQLite.
- Production deployment: PostgreSQL.

**Security Note:** Never commit `.env` files, API keys, database credentials, or payment secrets to GitHub.

### Step 5: Run Automated Tests

```powershell
.venv\Scripts\python.exe -m pytest backend/tests -v
```

The automated test suite includes unit and integration tests. External provider responses are mocked where applicable, allowing tests to run without consuming live Channel3 API credits.

### Step 6: Start the Backend Server

```powershell
.venv\Scripts\uvicorn.exe backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend will be available at:

| Service | URL |
|---|---|
| Swagger UI | http://127.0.0.1:8000/docs |
| ReDoc | http://127.0.0.1:8000/redoc |
| Health Check | http://127.0.0.1:8000/health |

### Step 7: Launch the Frontend

Open the frontend application through your local development server, such as the Visual Studio Code Live Server extension.

Ensure the frontend API configuration points to the locally running FastAPI backend:

```text
http://127.0.0.1:8000
```

---

## 7. Live Product Search API

BUYWISE AI integrates with the Channel3 REST API to retrieve live product information and merchant offers.

### Endpoint

```http
GET /api/products/search?q=wireless+headphones&limit=10
```

### Example Request

```bash
curl "http://127.0.0.1:8000/api/products/search?q=wireless%20headphones&limit=10"
```

### Response Information

The API returns normalized product information, including available fields such as:

- Product name.
- Product description.
- Product image.
- Current price.
- Original currency information, where available.
- Merchant and retailer offer details.
- Product provenance and provider information.

Live Channel3 results include provenance metadata such as:

```json
{
  "provider": "channel3",
  "is_synthetic": false
}
```

The exact response structure depends on the available provider data and backend normalization.

---

## 8. Spending Mandates and Purchase Validation

A central feature of BUYWISE AI is its separation of AI recommendations from purchasing authorization.

The platform uses a deterministic backend policy engine to validate purchasing operations against user-defined spending restrictions.

### Purchase Validation Workflow

```text
       USER SHOPPING REQUEST
                 |
                 v
       AI PRODUCT RECOMMENDATION
                 |
                 v
       BACKEND POLICY VALIDATION
                 |
           +-----+-----+
           |           |
           v           v
        VALID        INVALID
           |           |
           v           v
      CONTINUE       BLOCK OR
      CHECKOUT       REJECT
           |
           v
      PAYPAL SANDBOX
```

### Why Deterministic Enforcement?

AI-generated recommendations can help users discover suitable products, but they should not independently authorize purchases.

BUYWISE AI therefore ensures that:

- Spending restrictions are evaluated by backend logic.
- Configured budget limits remain authoritative.
- Product recommendations cannot independently override spending mandates.
- Purchasing operations must satisfy the applicable validation rules.

This creates a separation between intelligent recommendations and deterministic financial control.

---

## 9. Database Architecture

BUYWISE AI uses SQLite for local development and PostgreSQL for production deployment.

### Local Development – SQLite

- Lightweight relational database.
- Write-Ahead Logging (WAL) mode.
- Foreign key constraints enabled.
- SQLAlchemy-based database interaction.
- Supports local development and testing.

### Production – PostgreSQL

- Hosted through Neon PostgreSQL.
- Persistent cloud database storage.
- SQLAlchemy integration.
- Alembic-based schema migration management.
  
Production uses Neon PostgreSQL, configured through Render environment variables.
This approach provides a convenient local development environment while supporting persistent production data storage.

---

## 10. External API Integrations

### Channel3 – Product Aggregation

Channel3 provides live product discovery and retailer offer information.

Integration capabilities include:

- Product search through `POST /v1/search`.
- Product information retrieval through `GET /v1/products/{id}`.
- Live product aggregation.
- Merchant offer retrieval.

### Google Gemini – AI Shopping Intelligence

Gemini supports the AI-assisted shopping experience, including natural-language interactions and shopping recommendations.

### PayPal – Checkout Integration

Payments currently use the PayPal Sandbox environment. No real-money transactions are processed.

---

## 11. Deployment

BUYWISE AI is deployed using a cloud-based architecture.

| Component | Deployment Platform |
|---|---|
| Frontend | Vercel |
| Backend | Render |
| Production Database | Neon PostgreSQL |
| Product Aggregation | Channel3 API |
| AI Integration | Google Gemini |
| Payment Testing | PayPal Sandbox |

### Live Application Links

- **Live Website:** https://buywise-sand.vercel.app/
- **Backend API:** https://buywise-nd5j.onrender.com
- **Swagger Documentation:** https://buywise-nd5j.onrender.com/docs
- **Backend Health Check:** https://buywise-nd5j.onrender.com/health
- **GitHub Repository:** https://github.com/keerthi-codes/BuyWise

---

## 12. Testing and Reliability

BUYWISE AI includes an automated backend testing suite built using:

- **Pytest:** Unit and integration testing.
- **pytest-asyncio:** Asynchronous test support.
- **HTTPX:** HTTP client testing.

Run the complete test suite using:

```powershell
python -m pytest backend/tests -v
```

The test suite uses mocked external provider responses where applicable, allowing core application functionality to be tested without depending on live external API availability.

---

## 13. Security and Responsible Spending

BUYWISE AI incorporates several design considerations to support secure and responsible shopping:

- Backend-enforced spending mandates.
- Separation of AI recommendations and purchase authorization.
- Environment-based configuration for sensitive credentials.
- Structured API error handling.
- Persistent production database storage.
- PayPal Sandbox for payment workflow testing.

The spending policy engine is designed to ensure that user-defined financial restrictions remain independent of AI-generated recommendations.

---

## 14. Innovation and Hackathon Value

BUYWISE AI brings together three important capabilities into one intelligent shopping platform.

### 1. Intelligent Product Discovery
Aggregates live product information and competing retailer offers to simplify online shopping.

### 2. Transparent Product Evaluation
Provides structured comparisons of prices, specifications, and merchant offers to support informed purchasing decisions.

### 3. Deterministic Financial Control
Introduces backend-enforced spending mandates that ensure purchasing decisions remain within user-defined financial boundaries.

### What Makes BUYWISE AI Different?

Traditional shopping platforms primarily help users find products. BUYWISE AI goes further by combining AI-assisted discovery with independent spending-policy enforcement.

Its architecture ensures that AI can assist with recommendations without having authority to bypass the user's financial restrictions.

**The result is a shopping intelligence platform designed not only to help users find better products, but also to help them spend responsibly.**

---

## 15. Future Enhancements

Potential future improvements include:

- Integration with additional retailer and product data providers.
- Enhanced historical price analysis and trend visualization.
- More personalized shopping recommendations.
- Expanded shopping analytics and budgeting insights.
- Improved product discovery and recommendation relevance.

---

## 16. License and Acknowledgement

Developed as part of the PayPal AI Hackathon.

This project is currently maintained as a hackathon project.
No open-source license has been specified.

---

# BUYWISE AI

**Discover smarter. Compare deeper. Spend responsibly.**
Home / Explore:
<img width="1916" height="1027" alt="Screenshot 2026-11-10 193740" src="https://github.com/user-attachments/assets/f30cc54b-2cf4-46b0-a14c-80586a0886e0" />
Product Comparison:
<img width="1917" height="1031" alt="Screenshot 2026-11-10 193841" src="https://github.com/user-attachments/assets/dd25c08e-1046-4722-aaa4-ab8fad36e71f" />
AI Shopping Assistant:
<img width="1917" height="1030" alt="Screenshot 2026-11-10 194547" src="https://github.com/user-attachments/assets/cc171942-7adb-46c9-b97d-6ae0448c608a" />
Spending Mandates:
<img width="1917" height="1031" alt="Screenshot 2026-11-10 200834" src="https://github.com/user-attachments/assets/85e26e96-d561-4bc9-83f7-cc614ff9d7da" />
