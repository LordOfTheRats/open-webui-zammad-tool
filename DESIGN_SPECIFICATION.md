# Zammad Tool for Open WebUI - Design Specification

**Version:** 1.2.0  
**Author:** René Vögeli 
**License:** MIT  
**Last Updated:** 2025-12-29

---

## 1. Overview

### 1.1 Purpose

The Zammad Tool is an Open WebUI integration that provides comprehensive access to Zammad ticket system instances (self-hosted or cloud). It enables AI assistants to interact with Zammad tickets, articles (comments), users, organizations, and system metadata through natural language commands.

### 1.2 Key Features

- **Ticket Management**: List, create, read, and update tickets with flexible filtering
- **Article Operations**: Add and list ticket articles (comments/notes/emails)
- **User Management**: Search and retrieve user information
- **Organization Management**: Search and manage organizations
- **Report Profile Access**: Read report profiles created by admins for ticket analysis
- **Helper Endpoints**: Access ticket states, groups, and priorities
- **Compact Mode**: Configurable output mode to reduce response size while preserving essential information
- **Reliability Features**: Automatic retry logic with exponential backoff, rate limit handling, and jitter
- **Flexible Authentication**: Support for both API token and HTTP Basic authentication

### 1.3 Target Use Cases

- Ticket management automation through AI assistants
- Customer support assistance
- Ticket triage and assignment
- User and organization lookup
- Automated ticket workflows
- Support ticket reporting and analysis
- Reading and using admin-configured report profiles for data filtering

---

## 2. Architecture

### 2.1 Design Patterns

#### 2.1.1 Open WebUI Toolkit Pattern

The tool follows Open WebUI's toolkit architecture:

```python
class Tools:
    def __init__(self):
        self.valves = self.Valves()
    
    class Valves(BaseModel):
        # Configuration parameters
        pass
```

All public methods are exposed as callable tools to the AI assistant.

#### 2.1.2 Async/Await Pattern

All API interactions use Python's async/await pattern for non-blocking I/O operations.

#### 2.1.3 Type Safety

Strong typing throughout using:
- Type hints for all parameters and return values
- Pydantic models for configuration validation
- Type aliases for common patterns (`Json`, `TicketRef`)
- Literal types for enumerated values

### 2.2 Core Components

#### 2.2.1 Configuration System (Valves)

The `Valves` class provides type-safe configuration:

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `base_url` | str | "https://zammad.example.com" | Zammad instance URL |
| `token` | str | "" | API Token (recommended) |
| `username` | str | "" | Username for HTTP Basic Auth |
| `password` | str | "" | Password for HTTP Basic Auth |
| `verify_ssl` | bool | True | TLS certificate verification |
| `timeout_seconds` | float | 30.0 | HTTP request timeout |
| `per_page` | int | 20 | Default pagination size |
| `compact_results_default` | bool | True | Default compact mode setting |
| `max_retries` | int | 3 | Maximum retry attempts |
| `backoff_initial_seconds` | float | 0.8 | Initial retry delay |
| `backoff_max_seconds` | float | 10.0 | Maximum retry delay |
| `retry_jitter` | float | 0.2 | Jitter proportion for retry delays |
| `allow_public_articles` | bool | True | Allow creation of public articles. When disabled, forces all articles to be internal |

#### 2.2.2 HTTP Client Layer

**Request Handler** (`_request`):
- Automatic retry logic for transient failures (429, 502, 503, 504, timeouts)
- Exponential backoff with configurable jitter
- Respect for `Retry-After` headers
- Support for both JSON responses
- Flexible authentication (Token or HTTP Basic)

**Pagination Handler** (`_paginate`):
- 1-based page pagination (Zammad standard)
- Configurable page size
- Simple API for fetching pages

#### 2.2.3 Data Transformation Layer

**Compact Mode System**:
- Reduces response payload size while preserving critical information
- Different compact schemas per entity type
- Never removes body/note fields
- Configurable per-request or globally

**Entity Types with Compact Support**:
- Tickets
- Articles (Comments/Notes)
- Users
- Organizations
- Ticket States
- Groups
- Priorities
- Report Profiles

#### 2.2.4 Authentication & Authorization

- **Token Authentication**: API Token via `Authorization: Token token=...` header (recommended)
- **HTTP Basic Authentication**: Username and password via HTTP Basic Auth
- **Required Permissions**: Appropriate Zammad role permissions for operations

---

## 3. API Surface

### 3.1 Ticket Operations

#### 3.1.1 List Tickets
```python
async def zammad_list_tickets(
    state: Optional[str] = None,
    priority: Optional[str] = None,
    group: Optional[str] = None,
    customer_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: List tickets with optional filtering.

**Key Parameters**:
- `state`: Filter by ticket state (e.g., "new", "open", "closed")
- `priority`: Filter by priority (e.g., "2 normal", "3 high")
- `group`: Filter by group name
- `customer_id`: Filter by customer user ID
- `organization_id`: Filter by organization ID

**Compact Fields**: id, number, title, state, state_id, priority, priority_id, group, group_id, customer_id, owner_id, organization_id, created_at, updated_at, close_at, tags

#### 3.1.2 Get Ticket
```python
async def zammad_get_ticket(
    ticket_id: TicketRef,
    compact: Optional[bool] = None
) -> Json
```

**Purpose**: Retrieve a single ticket by ID.

#### 3.1.3 Create Ticket
```python
async def zammad_create_ticket(
    title: str,
    group: str,
    customer_id: Optional[int] = None,
    customer_email: Optional[str] = None,
    state: Optional[str] = None,
    priority: Optional[str] = None,
    owner_id: Optional[int] = None,
    article_body: Optional[str] = None,
    article_type: str = "note",
    article_internal: bool = False,
    compact: Optional[bool] = None,
) -> Json
```

**Purpose**: Create a new ticket.

**Key Features**:
- Can specify customer by ID or email (email uses "guess" feature)
- Optional initial article/comment
- Configurable article type and visibility

#### 3.1.4 Update Ticket
```python
async def zammad_update_ticket(
    ticket_id: TicketRef,
    title: Optional[str] = None,
    state: Optional[str] = None,
    priority: Optional[str] = None,
    group: Optional[str] = None,
    owner_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    compact: Optional[bool] = None,
) -> Json
```

**Purpose**: Update an existing ticket.

### 3.2 Ticket Article Operations

#### 3.2.1 List Ticket Articles
```python
async def zammad_list_ticket_articles(
    ticket_id: TicketRef,
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: List all articles (comments/notes/emails) for a ticket.

**Compact Fields**: id, ticket_id, type, sender, from, to, subject, body (preserved), content_type, internal, created_at, created_by_id

#### 3.2.2 Create Ticket Article
```python
async def zammad_create_ticket_article(
    ticket_id: TicketRef,
    body: str,
    type: str = "note",
    internal: bool = False,
    subject: Optional[str] = None,
    from_address: Optional[str] = None,
    to_address: Optional[str] = None,
    content_type: str = "text/html",
) -> Json
```

**Purpose**: Add a comment/note/email to a ticket.

**Key Parameters**:
- `type`: Article type - "note", "email", "phone", "web", etc.
- `internal`: Whether the article is internal (not visible to customer)
- `content_type`: "text/html" or "text/plain"

### 3.3 User Operations

#### 3.3.1 Search Users
```python
async def zammad_search_users(
    query: str,
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: Search users by name, email, or login.

**Compact Fields**: id, login, firstname, lastname, email, organization_id, active, created_at, updated_at

#### 3.3.2 Get User
```python
async def zammad_get_user(
    user_id: int,
    compact: Optional[bool] = None
) -> Json
```

**Purpose**: Retrieve a single user by ID.

#### 3.3.3 List Users
```python
async def zammad_list_users(
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: List all users.

### 3.4 Organization Operations

#### 3.4.1 List Organizations
```python
async def zammad_list_organizations(
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: List all organizations.

**Compact Fields**: id, name, note, active, created_at, updated_at

#### 3.4.2 Get Organization
```python
async def zammad_get_organization(
    organization_id: int,
    compact: Optional[bool] = None
) -> Json
```

**Purpose**: Retrieve a single organization by ID.

#### 3.4.3 Search Organizations
```python
async def zammad_search_organizations(
    query: str,
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: Search organizations by name.

### 3.5 Helper Lookup Endpoints

#### 3.5.1 List Ticket States
```python
async def zammad_list_ticket_states(
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: List all available ticket states.

**Compact Fields**: id, name, state_type, active

#### 3.5.2 List Groups
```python
async def zammad_list_groups(
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: List all ticket groups.

**Compact Fields**: id, name, active, note

#### 3.5.3 List Priorities
```python
async def zammad_list_priorities(
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: List all ticket priorities.

**Compact Fields**: id, name, active

### 3.6 Report Profile Operations

#### 3.6.1 List Report Profiles
```python
async def zammad_list_report_profiles(
    page: int = 1,
    per_page: Optional[int] = None,
    compact: Optional[bool] = None,
) -> list[Json]
```

**Purpose**: List all report profiles (created by admins). Requires 'report' permission.

**Key Features**:
- Access report profiles without needing admin permissions
- Retrieve configurations for filtering and analyzing ticket data
- Use pagination to manage large lists of profiles

**Compact Fields**: id, name, active, condition, created_at, updated_at

#### 3.6.2 Get Report Profile
```python
async def zammad_get_report_profile(
    report_profile_id: int,
    compact: Optional[bool] = None
) -> Json
```

**Purpose**: Retrieve a single report profile by ID. Requires 'report' permission.

**Key Features**:
- Fetch detailed configuration of a specific report profile
- View filtering conditions and settings
- Non-admin users can read profiles created by admins

---

## 4. Reliability & Error Handling

### 4.1 Retry Strategy

**Retryable Conditions**:
- HTTP 429 (Rate Limited)
- HTTP 502, 503, 504 (Gateway/Service errors)
- Connection timeouts
- Read timeouts
- Pool timeouts
- Connection errors

**Backoff Algorithm**:
- Exponential backoff (2^n)
- Respects `Retry-After` header
- Configurable maximum delay
- Jitter to prevent thundering herd
- Configurable retry count

### 4.2 Error Messages

All errors include:
- HTTP status code
- Request method and path
- Detailed error response from Zammad

**Example**:
```
Zammad API error 404 for GET /tickets/123: {"error": "Ticket not found"}
```

### 4.3 Safety Mechanisms

**Authentication Validation**:
```python
def _headers(self) -> dict[str, str]:
    if not self.valves.token and not (self.valves.username and self.valves.password):
        raise ValueError(
            "Zammad authentication is not set. Configure the tool Valves: token=... or username=... and password=..."
        )
    ...
```

**Public Article Control**:
The `allow_public_articles` valve provides a safety mechanism to prevent accidental creation of public articles:
- When `allow_public_articles=True` (default): Articles respect the `internal` parameter value
- When `allow_public_articles=False`: All articles are forced to be internal (`internal=True`) regardless of the parameter value
- This applies to both `zammad_create_ticket` (when creating initial article) and `zammad_create_ticket_article`
- Useful for environments where public customer-facing articles should be restricted

---

## 5. Integration Patterns

### 5.1 Open WebUI Usage

**Tool Registration**:
The tool is automatically discovered by Open WebUI when placed in the tools directory.

**Configuration**:
Users configure Valves through Open WebUI's admin interface:
1. Navigate to Tools settings
2. Find "Zammad Ticket System"
3. Configure `base_url` and either `token` or `username`/`password`

**Invocation**:
AI assistants invoke methods through natural language:
- "List all open tickets"
- "Create a ticket for customer john@example.com with title 'Login issue'"
- "Show me the comments on ticket 42"

### 5.2 Workflow Examples

**Ticket Triage Workflow**:
1. `zammad_list_tickets(state="new")` - Get new tickets
2. `zammad_search_users(query="agent name")` - Find agent
3. `zammad_update_ticket(ticket_id=123, owner_id=agent_id, state="open")` - Assign and open ticket

**Customer Support Workflow**:
1. `zammad_search_users(query="customer email")` - Find customer
2. `zammad_list_tickets(customer_id=user_id)` - Get customer's tickets
3. `zammad_list_ticket_articles(ticket_id=42)` - Review conversation
4. `zammad_create_ticket_article(ticket_id=42, body="Response...")` - Add response

**Ticket Creation Workflow**:
1. `zammad_search_organizations(query="Company Name")` - Find organization
2. `zammad_list_groups()` - Get available groups
3. `zammad_create_ticket(title="...", group="Support", customer_email="...")` - Create ticket

**Report Profile Workflow**:
1. `zammad_list_report_profiles()` - List all available report profiles
2. `zammad_get_report_profile(report_profile_id=1)` - Get specific profile details
3. Use the profile's filter conditions to understand ticket filtering criteria

---

## 6. Known Limitations

### 6.1 Current Limitations

1. **No Tag Management**: Cannot add/remove tags from tickets via dedicated endpoints
2. **No Attachment Support**: File attachments not currently implemented
3. **Limited Article Types**: Basic support for common article types
4. **No Custom Fields**: Custom ticket fields not explicitly handled
5. **No Time Accounting**: Time tracking features not implemented
6. **No Ticket Merging/Splitting**: Advanced ticket operations not supported
7. **No Report Profile Creation**: Can only read report profiles, not create/edit/delete them (admin function)

### 6.2 Future Enhancement Opportunities

1. **Attachment Support**: Add file upload/download for ticket articles
2. **Tag Management**: Add/remove ticket tags
3. **Custom Fields**: Support for custom ticket attributes
4. **Time Accounting**: Track time spent on tickets
5. **Advanced Search**: Implement Zammad's advanced search syntax
6. **Ticket Templates**: Support for ticket templates
7. **SLA Management**: Query and display SLA information
8. **Knowledge Base**: Access to Zammad knowledge base articles
9. **Report Generation**: Generate reports using report profile configurations
9. **Overviews**: Access to custom ticket overviews

---

## 7. Troubleshooting Guide

### 7.1 Common Issues

**"Zammad authentication is not set"**:
- **Cause**: Missing authentication credentials
- **Solution**: Configure either `token` or both `username` and `password` in Valves

**"Zammad API error 401"**:
- **Cause**: Invalid credentials or insufficient permissions
- **Solution**: Verify token/credentials and user permissions

**"Zammad API error 404"**:
- **Cause**: Resource not found or access denied
- **Solution**: Check ticket/user/organization ID and access permissions

**Connection timeout**:
- **Cause**: Network issues or slow Zammad server
- **Solution**: Increase `timeout_seconds` in Valves

---

## 8. Glossary

**Term** | **Definition**
---------|---------------
**Ticket** | Main support request object in Zammad
**Article** | Comment, note, email, or other communication on a ticket
**Agent** | User with support agent permissions
**Customer** | User who creates and interacts with tickets
**Group** | Team or department that handles tickets
**State** | Ticket status (new, open, closed, etc.)
**Priority** | Ticket urgency level
**Organization** | Company or entity associated with customers
**Internal Article** | Article visible only to agents, not customers
**Report Profile** | Configuration with filters for analyzing ticket data, created by admins

---

## 9. Appendix

### 9.1 Example Use Cases

**Automated Ticket Assignment**:
```
User: "Assign all new support tickets to Alice"
AI: 
1. zammad_search_users(query="Alice")
2. zammad_list_tickets(state="new", group="Support")
3. For each ticket: zammad_update_ticket(ticket_id=X, owner_id=alice_id)
```

**Customer Ticket History**:
```
User: "Show me all tickets for john@example.com"
AI:
1. zammad_search_users(query="john@example.com")
2. zammad_list_tickets(customer_id=user_id)
3. Display ticket summaries
```

**Ticket Status Report**:
```
User: "How many open tickets do we have?"
AI:
1. zammad_list_tickets(state="open")
2. Count and categorize by group/priority
3. Generate summary report
```

**Using Report Profiles**:
```
User: "What report profiles are available?"
AI:
1. zammad_list_report_profiles()
2. Display available profiles with their names and conditions
User: "Show me the details of the first report profile"
AI:
1. zammad_get_report_profile(report_profile_id=1)
2. Display detailed configuration and filter conditions
```

### 9.2 API Reference Links

**Zammad API Documentation**:
- Tickets API: https://docs.zammad.org/en/latest/api/ticket.html
- Users API: https://docs.zammad.org/en/latest/api/user.html
- Organizations API: https://docs.zammad.org/en/latest/api/organization.html
- REST API Overview: https://docs.zammad.org/en/latest/api/intro.html

### 9.3 Version History

**1.2.0** (Current):
- Added `allow_public_articles` valve for controlling public article creation
- Safety feature: when disabled, forces all articles to be internal regardless of parameter value
- Applies to both ticket creation with initial article and standalone article creation

**1.1.0**:
- Set article_internal and internal to True by default

**1.0.0**:
- Initial release
- Core ticket operations
- Article management
- User and organization search
- Helper lookup endpoints
- Compact mode support
- Retry logic with exponential backoff

**Future Roadmap**:
- Attachment support
- Tag management
- Custom fields
- Time accounting
- Knowledge base integration
- Advanced search

---

**Document End**

This specification reflects the current state of the Zammad Tool for Open WebUI version 1.0.0. This tool is adapted from the GitLab Tool pattern for use with Zammad ticket systems.