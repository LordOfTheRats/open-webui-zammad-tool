"""
title: Zammad Ticket System - Tickets / Users / Organizations / Reports
author: René Vögeli
author_url: https://github.com/LordOfTheRats
git_url: https://github.com/LordOfTheRats/open-webui-zammad-tool
description: Access Zammad ticket system from Open WebUI. Work with tickets, articles (comments), users, organizations, ticket states, groups, and report profiles. Supports compact output mode and basic retry/rate-limit handling. Features event emitter integration for status messages, citations, errors, and confirmations.
required_open_webui_version: 0.4.0
requirements: httpx
version: 1.3.0
licence: MIT
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

Json = dict[str, Any]
TicketRef = int  # Ticket ID


class OperationCancelledError(Exception):
    """Raised when a user cancels an operation via confirmation dialog."""
    pass


# ----------------------------
# Helper functions
# ----------------------------


def _api_base(valves) -> str:
    """Get the API base URL from valves configuration."""
    return valves.base_url.rstrip("/") + "/api/v1"


def _headers(valves) -> dict[str, str]:
    """Generate HTTP headers for API requests."""
    headers = {"Content-Type": "application/json"}

    if valves.token:
        headers["Authorization"] = f"Token token={valves.token}"
    elif valves.username and valves.password:
        # HTTP Basic Auth will be handled by httpx.BasicAuth
        pass
    else:
        raise ValueError(
            "Zammad authentication is not set. Configure the tool Valves: token=... or username=... and password=..."
        )

    return headers


def _want_compact(valves, compact: Optional[bool]) -> bool:
    """Determine if compact mode should be used."""
    return valves.compact_results_default if compact is None else bool(compact)


def _user_brief(u: Any) -> Optional[Json]:
    """Extract brief user information."""
    if not isinstance(u, dict):
        return None
    return {
        "id": u.get("id"),
        "firstname": u.get("firstname"),
        "lastname": u.get("lastname"),
        "email": u.get("email"),
        "login": u.get("login"),
    }


def _compact_one(kind: str, obj: Any) -> Any:
    """Compact a single object based on its kind."""
    if not isinstance(obj, dict):
        return obj

    if kind == "ticket":
        return {
            "id": obj.get("id"),
            "number": obj.get("number"),
            "title": obj.get("title"),
            "state": obj.get("state"),
            "state_id": obj.get("state_id"),
            "priority": obj.get("priority"),
            "priority_id": obj.get("priority_id"),
            "group": obj.get("group"),
            "group_id": obj.get("group_id"),
            "customer_id": obj.get("customer_id"),
            "owner_id": obj.get("owner_id"),
            "organization_id": obj.get("organization_id"),
            "created_at": obj.get("created_at"),
            "updated_at": obj.get("updated_at"),
            "close_at": obj.get("close_at"),
            "tags": obj.get("tags"),
            "article_count": obj.get("article_count"),
        }

    if kind == "article":
        # NOTE: In compact mode we STILL include body (it's the core of a comment).
        return {
            "id": obj.get("id"),
            "ticket_id": obj.get("ticket_id"),
            "type": obj.get("type"),
            "sender": obj.get("sender"),
            "from": obj.get("from"),
            "to": obj.get("to"),
            "subject": obj.get("subject"),
            "body": obj.get("body"),
            "content_type": obj.get("content_type"),
            "internal": obj.get("internal"),
            "created_at": obj.get("created_at"),
            "created_by_id": obj.get("created_by_id"),
        }

    if kind == "user":
        return {
            "id": obj.get("id"),
            "login": obj.get("login"),
            "firstname": obj.get("firstname"),
            "lastname": obj.get("lastname"),
            "email": obj.get("email"),
            "organization_id": obj.get("organization_id"),
            "active": obj.get("active"),
            "created_at": obj.get("created_at"),
            "updated_at": obj.get("updated_at"),
        }

    if kind == "organization":
        return {
            "id": obj.get("id"),
            "name": obj.get("name"),
            "note": obj.get("note"),
            "active": obj.get("active"),
            "created_at": obj.get("created_at"),
            "updated_at": obj.get("updated_at"),
        }

    if kind == "state":
        return {
            "id": obj.get("id"),
            "name": obj.get("name"),
            "state_type": obj.get("state_type"),
            "active": obj.get("active"),
        }

    if kind == "group":
        return {
            "id": obj.get("id"),
            "name": obj.get("name"),
            "active": obj.get("active"),
            "note": obj.get("note"),
        }

    if kind == "priority":
        return {
            "id": obj.get("id"),
            "name": obj.get("name"),
            "active": obj.get("active"),
        }

    if kind == "report_profile":
        return {
            "id": obj.get("id"),
            "name": obj.get("name"),
            "active": obj.get("active"),
            "condition": obj.get("condition"),
            "created_at": obj.get("created_at"),
            "updated_at": obj.get("updated_at"),
        }

    return obj


def _maybe_compact(kind: str, data: Any, valves, compact: Optional[bool]) -> Any:
    """Apply compact mode to data if requested."""
    if not _want_compact(valves, compact):
        return data
    if isinstance(data, list):
        return [_compact_one(kind, x) for x in data]
    return _compact_one(kind, data)


def _compute_delay(valves, attempt: int, retry_after: Optional[float] = None) -> float:
    """Compute retry delay with exponential backoff and jitter."""
    if retry_after is not None and retry_after > 0:
        base = float(retry_after)
    else:
        base = float(valves.backoff_initial_seconds) * (2 ** (attempt - 1))

    base = min(base, float(valves.backoff_max_seconds))

    jitter = float(valves.retry_jitter)
    if jitter > 0:
        delta = base * jitter
        base = base + random.uniform(-delta, delta)

    return max(0.0, base)


async def _emit_status(
    event_emitter: Optional[Any],
    description: str,
    done: bool = False,
    hidden: bool = False,
) -> None:
    """Emit a status event to Open WebUI."""
    if event_emitter:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "description": description,
                    "done": done,
                    "hidden": hidden,
                },
            }
        )


async def _emit_citation(
    event_emitter: Optional[Any],
    name: str,
    url: str,
    content: str,
) -> None:
    """Emit a citation event to Open WebUI with actual content."""
    if event_emitter:
        citation_data = {
            "type": "citation",
            "data": {
                "document": [content],
                "metadata": [{"source": url, "name": name}],
                "source": {"name": name},
            },
        }
        await event_emitter(citation_data)


def _format_for_citation(data: Any) -> str:
    """Format data as JSON string for citation content."""
    return json.dumps(data, indent=2, ensure_ascii=False)


async def _emit_error(
    event_emitter: Optional[Any],
    error_message: str,
) -> None:
    """Emit an error event to Open WebUI."""
    if event_emitter:
        await event_emitter(
            {
                "type": "chat:message:error",
                "data": {
                    "content": error_message,
                },
            }
        )


async def _request_confirmation(
    event_call: Optional[Any],
    title: str,
    message: str,
) -> bool:
    """
    Request user confirmation via Open WebUI event call.
    
    Returns True if the user confirms or if event_call is not available
    (which allows operations to proceed normally when event system is not active).
    Returns False if the user declines the confirmation.
    
    The event_call may return either boolean True or the string "confirmed"
    depending on the Open WebUI version/configuration.
    
    Note: When event_call is None, the function returns True, allowing the
    operation to proceed. This default behavior assumes confirmations are only
    required when explicitly enabled via the require_confirmation_for_write_ops valve.
    """
    if not event_call:
        return True
    
    result = await event_call(
        {
            "type": "confirmation",
            "data": {
                "title": title,
                "message": message,
            },
        }
    )
    return result is True or result == "confirmed"


async def _request(
        valves,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
) -> Any:
    """Make an HTTP request to the Zammad API with retry logic."""
    url = _api_base(valves) + path
    headers = _headers(valves)

    max_retries = max(0, int(valves.max_retries))

    # Prepare auth
    auth = None
    if valves.username and valves.password and not valves.token:
        auth = httpx.BasicAuth(valves.username, valves.password)

    async with httpx.AsyncClient(
            verify=valves.verify_ssl,
            timeout=valves.timeout_seconds,
            headers=headers,
            auth=auth,
    ) as client:
        for attempt in range(0, max_retries + 1):
            try:
                r = await client.request(method, url, params=params, json=json)

                if r.status_code in (429, 502, 503, 504) and attempt < max_retries:
                    retry_after_hdr = r.headers.get("Retry-After")
                    retry_after: Optional[float] = None
                    if retry_after_hdr:
                        try:
                            retry_after = float(retry_after_hdr)
                        except Exception:
                            retry_after = None
                    delay = _compute_delay(
                        valves, attempt=attempt + 1, retry_after=retry_after
                    )
                    await asyncio.sleep(delay)
                    continue

                if r.status_code >= 400:
                    try:
                        detail = r.json()
                    except Exception:
                        detail = r.text
                    raise RuntimeError(
                        f"Zammad API error {r.status_code} for {method} {path}: {detail}"
                    )

                if r.status_code == 204:
                    return {"ok": True}

                if not r.text:
                    return {"ok": True}

                return r.json()

            except (
                    httpx.ConnectTimeout,
                    httpx.ReadTimeout,
                    httpx.PoolTimeout,
                    httpx.ConnectError,
            ) as e:
                if attempt < max_retries:
                    delay = _compute_delay(valves, attempt=attempt + 1, retry_after=None)
                    await asyncio.sleep(delay)
                    continue
                raise e


async def _paginate(
        valves,
        path: str,
        params: Optional[dict[str, Any]] = None,
        page: int = 1,
        per_page: Optional[int] = None,
) -> list[Any]:
    """
    Paginate API requests using client-side pagination.
    
    Fetches all results from the API and returns the requested page.
    This approach is used because some Zammad API endpoints (like ticket_articles)
    do not support server-side pagination parameters.
    
    Args:
        valves: Configuration valves
        path: API endpoint path
        params: Query parameters
        page: Page number (1-based)
        per_page: Items per page
    
    Returns:
        List of results for the requested page
    """
    page = int(page)
    if page < 1:
        raise ValueError("page must be >= 1")

    per_page = int(per_page)
    effective_per_page = per_page or valves.per_page
    if effective_per_page < 1:
        raise ValueError("per_page must be >= 1")

    # Fetch all results (Zammad endpoints don't consistently support server-side pagination)
    params = dict(params or {})
    result = await _request(valves, "GET", path, params=params)
    
    if not isinstance(result, list):
        return [result]
    
    # Apply client-side pagination
    start_idx = (page - 1) * effective_per_page
    end_idx = start_idx + effective_per_page
    return result[start_idx:end_idx]


class Tools:
    """
    Open WebUI Toolkit for Zammad Ticket System.
    """

    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        base_url: str = Field(
            "https://zammad.example.com",
            description="Base URL for your Zammad instance, e.g. https://zammad.example.com",
        )
        token: str = Field(
            "",
            description="Zammad API Token (recommended) or leave empty to use username/password",
        )
        username: str = Field(
            "",
            description="Zammad username (only needed if token is not set)",
        )
        password: str = Field(
            "",
            description="Zammad password (only needed if token is not set)",
        )
        verify_ssl: bool = Field(
            True,
            description="Verify TLS certificates (disable only for lab/self-signed setups)",
        )
        timeout_seconds: float = Field(
            30.0,
            description="HTTP request timeout in seconds",
        )
        per_page: int = Field(
            20,
            description="Default page size for list endpoints (Zammad pagination)",
        )
        compact_results_default: bool = Field(
            True,
            description=(
                "Default compact mode for responses. "
                "When true, tool returns a reduced field set (but still includes important content)."
            ),
        )

        # Retry / rate-limit handling
        max_retries: int = Field(
            3,
            description="Max retries for transient failures (429/502/503/504/timeouts). 0 disables retries.",
        )
        backoff_initial_seconds: float = Field(
            0.8,
            description="Initial backoff delay for retries (seconds).",
        )
        backoff_max_seconds: float = Field(
            10.0,
            description="Maximum backoff delay (seconds).",
        )
        retry_jitter: float = Field(
            0.2,
            description="Adds +/- jitter proportion of delay to spread retries (0.2 = +/-20%).",
        )
        allow_public_articles: bool = Field(
            True,
            description="Allow creation of public articles. When disabled, all articles are forced to be internal (not visible to customers) regardless of the internal parameter value.",
        )
        require_confirmation_for_write_ops: bool = Field(
            False,
            description="Require user confirmation before executing write operations (create, update). When enabled, user will be prompted to confirm each write operation.",
        )

    # ----------------------------
    # Ticket Operations
    # ----------------------------

    async def zammad_list_tickets(
            self,
            state: Optional[str] = None,
            priority: Optional[str] = None,
            group: Optional[str] = None,
            customer_id: Optional[int] = None,
            organization_id: Optional[int] = None,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        List tickets with optional filtering.

        Args:
          state: Filter by ticket state name (e.g., "new", "open", "closed").
          priority: Filter by priority name (e.g., "1 low", "2 normal", "3 high").
          group: Filter by group name.
          customer_id: Filter by customer user ID.
            If you only have a username or name, resolve it first via zammad_search_users(search="...") and use "id".
          organization_id: Filter by organization ID.
            If you only have a name, resolve it first via zammad_list_organizations(search="...") and use "id".
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, "📋 Listing tickets from Zammad...", done=False)
            
            params: dict[str, Any] = {}

            # Build search query if filters provided
            search_filters = []
            if state:
                search_filters.append(f"state:{state}")
            if priority:
                search_filters.append(f"priority:{priority}")
            if group:
                search_filters.append(f"group:{group}")
            if customer_id:
                search_filters.append(f"customer_id:{customer_id}")
            if organization_id:
                search_filters.append(f"organization_id:{organization_id}")

            if search_filters:
                params["query"] = " AND ".join(search_filters)

            data = await _paginate(self.valves, "/tickets", params=params, page=page, per_page=per_page)
            result = _maybe_compact("ticket", data, self.valves, compact)
            
            # Emit citation for the Zammad source with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Tickets",
                url=f"{base_url}/tickets",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved {len(result)} tickets", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to list tickets: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_get_ticket(
            self, ticket_id: TicketRef, compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> Json:
        """
        Get a single ticket by ID.

        Args:
          ticket_id: Ticket ID.
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, f"🎫 Fetching ticket #{ticket_id}...", done=False)
            
            data = await _request(self.valves, "GET", f"/tickets/{ticket_id}")
            result = _maybe_compact("ticket", data, self.valves, compact)
            
            # Emit citation for the specific ticket with actual data
            base_url = self.valves.base_url.rstrip("/")
            ticket_url = f"{base_url}/#ticket/zoom/{ticket_id}"
            await _emit_citation(
                __event_emitter__,
                name=f"Ticket #{ticket_id}",
                url=ticket_url,
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved ticket #{ticket_id}", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to get ticket #{ticket_id}: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_create_ticket(
            self,
            title: str,
            group: str,
            customer_id: Optional[int] = None,
            customer_email: Optional[str] = None,
            state: Optional[str] = None,
            priority: Optional[str] = None,
            owner_id: Optional[int] = None,
            article_body: Optional[str] = None,
            article_type: str = "note",
            article_internal: bool = True,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
            __event_call__: Optional[Any] = None,
    ) -> Json:
        """
        Create a ticket.

        Args:
          title: Ticket title.
          group: Group name (required).
          customer_id: Customer user ID.
            If you only have a username or name, resolve it first via zammad_search_users(search="...") and use "id".
          customer_email: Alternative to customer_id - email to create/identify customer.
          state: Ticket state name (e.g., "new", "open").
          priority: Priority name (e.g., "2 normal").
          owner_id: Agent owner user ID.
            If you only have a username or name, resolve it first via zammad_search_users(search="...") and use "id".
          article_body: Initial article/comment body (optional).
          article_type: Article type (default: "note"). Options: "note", "email", "phone", etc.
          article_internal: If true, article is internal (not visible to customer, default: True).
          compact: If true, tool returns a reduced field set.
        """
        try:
            # Request confirmation if enabled
            if self.valves.require_confirmation_for_write_ops:
                await _emit_status(__event_emitter__, "🤔 Requesting confirmation to create ticket...", done=False)
                confirmation_msg = f"Create ticket '{title}' in group '{group}'?"
                if not await _request_confirmation(__event_call__, "Create Ticket", confirmation_msg):
                    await _emit_status(__event_emitter__, "❌ Ticket creation cancelled by user", done=True, hidden=True)
                    raise OperationCancelledError("Ticket creation cancelled by user")
            
            await _emit_status(__event_emitter__, f"🎫 Creating ticket '{title}'...", done=False)
            
            payload: dict[str, Any] = {
                "title": title,
                "group": group,
            }

            if customer_id is not None:
                payload["customer_id"] = customer_id
            elif customer_email:
                payload["customer_id"] = f"guess:{customer_email}"

            if state:
                payload["state"] = state
            if priority:
                payload["priority"] = priority
            if owner_id is not None:
                payload["owner_id"] = owner_id

            # Add article if body provided
            if article_body:
                # Enforce internal=True if public articles are not allowed
                effective_internal = article_internal if self.valves.allow_public_articles else True
                payload["article"] = {
                    "body": article_body,
                    "type": article_type,
                    "internal": effective_internal,
                }

            data = await _request(self.valves, "POST", "/tickets", json=payload)
            result = _maybe_compact("ticket", data, self.valves, compact)
            
            # Emit citation for the newly created ticket with actual data
            base_url = self.valves.base_url.rstrip("/")
            ticket_id = result.get("id", "unknown")
            ticket_url = f"{base_url}/#ticket/zoom/{ticket_id}"
            await _emit_citation(
                __event_emitter__,
                name=f"Created Ticket #{ticket_id}",
                url=ticket_url,
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully created ticket #{ticket_id}", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to create ticket: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_update_ticket(
            self,
            ticket_id: TicketRef,
            title: Optional[str] = None,
            state: Optional[str] = None,
            priority: Optional[str] = None,
            group: Optional[str] = None,
            owner_id: Optional[int] = None,
            customer_id: Optional[int] = None,
            organization_id: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
            __event_call__: Optional[Any] = None,
    ) -> Json:
        """
        Update a ticket.

        Args:
          ticket_id: Ticket ID.
          title: New title (or None to keep).
          state: New state name (or None to keep).
          priority: New priority name (or None to keep).
          group: New group name (or None to keep).
          owner_id: New owner user ID (or None to keep).
            If you only have a username or name, resolve it first via zammad_search_users(search="...") and use "id".
          customer_id: New customer user ID (or None to keep).
            If you only have a username or name, resolve it first via zammad_search_users(search="...") and use "id".
          organization_id: New organization ID (or None to keep).
            If you only have a username or name, resolve it first via zammad_search_users(search="...") and use "id".
          compact: If true, tool returns a reduced field set.
        """
        try:
            # Request confirmation if enabled
            if self.valves.require_confirmation_for_write_ops:
                await _emit_status(__event_emitter__, "🤔 Requesting confirmation to update ticket...", done=False)
                confirmation_msg = f"Update ticket #{ticket_id}?"
                if not await _request_confirmation(__event_call__, "Update Ticket", confirmation_msg):
                    await _emit_status(__event_emitter__, "❌ Ticket update cancelled by user", done=True, hidden=True)
                    raise OperationCancelledError("Ticket update cancelled by user")
            
            await _emit_status(__event_emitter__, f"✏️ Updating ticket #{ticket_id}...", done=False)
            
            payload: dict[str, Any] = {}

            if title is not None:
                payload["title"] = title
            if state is not None:
                payload["state"] = state
            if priority is not None:
                payload["priority"] = priority
            if group is not None:
                payload["group"] = group
            if owner_id is not None:
                payload["owner_id"] = owner_id
            if customer_id is not None:
                payload["customer_id"] = customer_id
            if organization_id is not None:
                payload["organization_id"] = organization_id

            data = await _request(
                self.valves,
                "PUT",
                f"/tickets/{ticket_id}",
                json=payload if payload else None,
            )
            result = _maybe_compact("ticket", data, self.valves, compact)
            
            # Emit citation for the updated ticket with actual data
            base_url = self.valves.base_url.rstrip("/")
            ticket_url = f"{base_url}/#ticket/zoom/{ticket_id}"
            await _emit_citation(
                __event_emitter__,
                name=f"Updated Ticket #{ticket_id}",
                url=ticket_url,
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully updated ticket #{ticket_id}", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to update ticket #{ticket_id}: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    # ----------------------------
    # Ticket Article Operations (Comments/Notes)
    # ----------------------------

    async def zammad_list_ticket_articles(
            self,
            ticket_id: TicketRef,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        List articles (comments/notes) for a ticket.

        Args:
          ticket_id: Ticket ID.
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set (still includes body).
        """
        try:
            await _emit_status(__event_emitter__, f"💬 Fetching articles for ticket #{ticket_id}...", done=False)
            
            data = await _paginate(
                self.valves,
                f"/ticket_articles/by_ticket/{ticket_id}",
                page=page,
                per_page=per_page,
            )
            result = _maybe_compact("article", data, self.valves, compact)
            
            # Emit citation for the ticket articles with actual data
            base_url = self.valves.base_url.rstrip("/")
            ticket_url = f"{base_url}/#ticket/zoom/{ticket_id}"
            await _emit_citation(
                __event_emitter__,
                name=f"Ticket #{ticket_id} Articles",
                url=ticket_url,
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved {len(result)} articles", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to list articles for ticket #{ticket_id}: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_create_ticket_article(
            self,
            ticket_id: TicketRef,
            body: str,
            type: str = "note",
            internal: bool = True,
            subject: Optional[str] = None,
            from_address: Optional[str] = None,
            to_address: Optional[str] = None,
            content_type: str = "text/html",
            __event_emitter__: Optional[Any] = None,
            __event_call__: Optional[Any] = None,
    ) -> Json:
        """
        Add an article (comment/note) to a ticket.

        Args:
          ticket_id: Ticket ID.
          body: Article body content.
          type: Article type. Options: "note" (default), "email", "phone", "web", etc.
          internal: If true, article is internal (not visible to customer, default: True).
          subject: Subject line (mainly for email type).
          from_address: From address (for email type).
          to_address: To address (for email type).
          content_type: Content type: "text/html" (default) or "text/plain".
        """
        try:
            # Request confirmation if enabled
            if self.valves.require_confirmation_for_write_ops:
                await _emit_status(__event_emitter__, "🤔 Requesting confirmation to add article...", done=False)
                confirmation_msg = f"Add article to ticket #{ticket_id}?"
                if not await _request_confirmation(__event_call__, "Add Article", confirmation_msg):
                    await _emit_status(__event_emitter__, "❌ Article creation cancelled by user", done=True, hidden=True)
                    raise OperationCancelledError("Article creation cancelled by user")
            
            await _emit_status(__event_emitter__, f"💬 Adding article to ticket #{ticket_id}...", done=False)
            
            # Enforce internal=True if public articles are not allowed
            effective_internal = internal if self.valves.allow_public_articles else True

            payload: dict[str, Any] = {
                "ticket_id": ticket_id,
                "body": body,
                "type": type,
                "internal": effective_internal,
                "content_type": content_type,
            }

            if subject:
                payload["subject"] = subject
            if from_address:
                payload["from"] = from_address
            if to_address:
                payload["to"] = to_address

            result = await _request(self.valves, "POST", "/ticket_articles", json=payload)
            
            # Emit citation for the new article with actual data
            base_url = self.valves.base_url.rstrip("/")
            ticket_url = f"{base_url}/#ticket/zoom/{ticket_id}"
            await _emit_citation(
                __event_emitter__,
                name=f"Ticket #{ticket_id} - New Article",
                url=ticket_url,
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully added article to ticket #{ticket_id}", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to create article for ticket #{ticket_id}: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    # ----------------------------
    # User Operations
    # ----------------------------

    async def zammad_search_users(
            self,
            search: str,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        Search users by name, email, or login.

        Args:
          search: Search query string.
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, f"🔍 Searching users for '{search}'...", done=False)
            
            params = {"query": search}
            data = await _paginate(self.valves, "/users/search", params=params, page=page, per_page=per_page)
            result = _maybe_compact("user", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Users Search",
                url=f"{base_url}/users",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Found {len(result)} users", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to search users: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_get_user(
            self, user_id: int, compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> Json:
        """
        Get a user by ID.

        Args:
          user_id: User ID.
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, f"👤 Fetching user #{user_id}...", done=False)
            
            data = await _request(self.valves, "GET", f"/users/{user_id}")
            result = _maybe_compact("user", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name=f"User #{user_id}",
                url=f"{base_url}/#user/profile/{user_id}",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved user #{user_id}", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to get user #{user_id}: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_list_users(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        List all users.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, "👥 Listing users...", done=False)
            
            data = await _paginate(self.valves, "/users", page=page, per_page=per_page)
            result = _maybe_compact("user", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Users",
                url=f"{base_url}/users",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved {len(result)} users", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to list users: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    # ----------------------------
    # Organization Operations
    # ----------------------------

    async def zammad_list_organizations(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        List organizations.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, "🏢 Listing organizations...", done=False)
            
            data = await _paginate(self.valves, "/organizations", page=page, per_page=per_page)
            result = _maybe_compact("organization", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Organizations",
                url=f"{base_url}/organizations",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved {len(result)} organizations", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to list organizations: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_get_organization(
            self, organization_id: int, compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> Json:
        """
        Get an organization by ID.

        Args:
          organization_id: Organization ID.
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, f"🏢 Fetching organization #{organization_id}...", done=False)
            
            data = await _request(self.valves, "GET", f"/organizations/{organization_id}")
            result = _maybe_compact("organization", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name=f"Organization #{organization_id}",
                url=f"{base_url}/#organization/profile/{organization_id}",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved organization #{organization_id}", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to get organization #{organization_id}: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_search_organizations(
            self,
            search: str,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        Search organizations by name.

        Args:
          search: Search query string.
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, f"🔍 Searching organizations for '{search}'...", done=False)
            
            params = {"query": search}
            data = await _paginate(self.valves, "/organizations/search", params=params, page=page, per_page=per_page)
            result = _maybe_compact("organization", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Organizations Search",
                url=f"{base_url}/organizations",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Found {len(result)} organizations", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to search organizations: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    # ----------------------------
    # Helper Lookup Endpoints
    # ----------------------------

    async def zammad_list_ticket_states(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        List ticket states.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, "🏷️ Listing ticket states...", done=False)
            
            data = await _paginate(self.valves, "/ticket_states", page=page, per_page=per_page)
            result = _maybe_compact("state", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Ticket States",
                url=f"{base_url}/api/v1/ticket_states",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved {len(result)} ticket states", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to list ticket states: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_list_groups(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        List groups.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, "👥 Listing groups...", done=False)
            
            data = await _paginate(self.valves, "/groups", page=page, per_page=per_page)
            result = _maybe_compact("group", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Groups",
                url=f"{base_url}/api/v1/groups",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved {len(result)} groups", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to list groups: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_list_priorities(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        List ticket priorities.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, "🎯 Listing priorities...", done=False)
            
            data = await _paginate(self.valves, "/ticket_priorities", page=page, per_page=per_page)
            result = _maybe_compact("priority", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Priorities",
                url=f"{base_url}/api/v1/ticket_priorities",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved {len(result)} priorities", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to list priorities: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    # ----------------------------
    # Report Profile Operations
    # ----------------------------

    async def zammad_list_report_profiles(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> list[Json]:
        """
        List report profiles (created by admins). Requires 'report' permission.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, "📊 Listing report profiles...", done=False)
            
            data = await _paginate(self.valves, "/report_profiles", page=page, per_page=per_page)
            result = _maybe_compact("report_profile", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name="Zammad Report Profiles",
                url=f"{base_url}/api/v1/report_profiles",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved {len(result)} report profiles", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to list report profiles: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise

    async def zammad_get_report_profile(
            self, report_profile_id: int, compact: Optional[bool] = None,
            __event_emitter__: Optional[Any] = None,
    ) -> Json:
        """
        Get a report profile by ID. Requires 'report' permission.

        Args:
          report_profile_id: Report profile ID.
          compact: If true, tool returns a reduced field set.
        """
        try:
            await _emit_status(__event_emitter__, f"📊 Fetching report profile #{report_profile_id}...", done=False)
            
            data = await _request(self.valves, "GET", f"/report_profiles/{report_profile_id}")
            result = _maybe_compact("report_profile", data, self.valves, compact)
            
            # Emit citation with actual data
            base_url = self.valves.base_url.rstrip("/")
            await _emit_citation(
                __event_emitter__,
                name=f"Report Profile #{report_profile_id}",
                url=f"{base_url}/api/v1/report_profiles/{report_profile_id}",
                content=_format_for_citation(result)
            )
            
            await _emit_status(__event_emitter__, f"✅ Successfully retrieved report profile #{report_profile_id}", done=True, hidden=True)
            return result
        except Exception as e:
            error_msg = f"Failed to get report profile #{report_profile_id}: {str(e)}"
            await _emit_error(__event_emitter__, error_msg)
            raise
