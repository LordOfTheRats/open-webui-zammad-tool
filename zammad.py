"""
title: Zammad Ticket System - Tickets / Users / Organizations / Reports
author: René Vögeli
author_url: https://github.com/LordOfTheRats
git_url: https://github.com/LordOfTheRats/open-webui-zammad-tool
description: Access Zammad ticket system from Open WebUI. Work with tickets, articles (comments), users, organizations, ticket states, groups, and report profiles. Supports compact output mode and basic retry/rate-limit handling.
required_open_webui_version: 0.4.0
requirements: httpx
version: 1.2.0
licence: MIT
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

Json = dict[str, Any]
TicketRef = int  # Ticket ID


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

    # ----------------------------
    # Internal helpers
    # ----------------------------

    def _api_base(self) -> str:
        return self.valves.base_url.rstrip("/") + "/api/v1"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}

        if self.valves.token:
            headers["Authorization"] = f"Token token={self.valves.token}"
        elif self.valves.username and self.valves.password:
            # HTTP Basic Auth will be handled by httpx.BasicAuth
            pass
        else:
            raise ValueError(
                "Zammad authentication is not set. Configure the tool Valves: token=... or username=... and password=..."
            )

        return headers

    def _want_compact(self, compact: Optional[bool]) -> bool:
        return self.valves.compact_results_default if compact is None else bool(compact)

    def _user_brief(self, u: Any) -> Optional[Json]:
        if not isinstance(u, dict):
            return None
        return {
            "id": u.get("id"),
            "firstname": u.get("firstname"),
            "lastname": u.get("lastname"),
            "email": u.get("email"),
            "login": u.get("login"),
        }

    def _compact_one(self, kind: str, obj: Any) -> Any:
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

    def _maybe_compact(self, kind: str, data: Any, compact: Optional[bool]) -> Any:
        if not self._want_compact(compact):
            return data
        if isinstance(data, list):
            return [self._compact_one(kind, x) for x in data]
        return self._compact_one(kind, data)

    def _compute_delay(
            self, attempt: int, retry_after: Optional[float] = None
    ) -> float:
        if retry_after is not None and retry_after > 0:
            base = float(retry_after)
        else:
            base = float(self.valves.backoff_initial_seconds) * (2 ** (attempt - 1))

        base = min(base, float(self.valves.backoff_max_seconds))

        jitter = float(self.valves.retry_jitter)
        if jitter > 0:
            delta = base * jitter
            base = base + random.uniform(-delta, delta)

        return max(0.0, base)

    async def _request(
            self,
            method: str,
            path: str,
            params: Optional[dict[str, Any]] = None,
            json: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = self._api_base() + path
        headers = self._headers()

        max_retries = max(0, int(self.valves.max_retries))

        # Prepare auth
        auth = None
        if self.valves.username and self.valves.password and not self.valves.token:
            auth = httpx.BasicAuth(self.valves.username, self.valves.password)

        async with httpx.AsyncClient(
                verify=self.valves.verify_ssl,
                timeout=self.valves.timeout_seconds,
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
                        delay = self._compute_delay(
                            attempt=attempt + 1, retry_after=retry_after
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
                        delay = self._compute_delay(
                            attempt=attempt + 1, retry_after=None
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise e

    async def _paginate(
            self,
            path: str,
            params: Optional[dict[str, Any]] = None,
            page: int = 1,
            per_page: Optional[int] = None,
    ) -> list[Any]:
        if page < 1:
            raise ValueError("page must be >= 1")

        params = dict(params or {})
        params["page"] = page
        params["per_page"] = per_page or self.valves.per_page

        result = await self._request("GET", path, params=params)

        if not isinstance(result, list):
            return [result]

        return result

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
    ) -> list[Json]:
        """
        List tickets with optional filtering.

        Args:
          state: Filter by ticket state name (e.g., "new", "open", "closed").
          priority: Filter by priority name (e.g., "1 low", "2 normal", "3 high").
          group: Filter by group name.
          customer_id: Filter by customer user ID.
          organization_id: Filter by organization ID.
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
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

        data = await self._paginate("/tickets", params=params, page=page, per_page=per_page)
        return self._maybe_compact("ticket", data, compact)

    async def zammad_get_ticket(
            self, ticket_id: TicketRef, compact: Optional[bool] = None
    ) -> Json:
        """
        Get a single ticket by ID.

        Args:
          ticket_id: Ticket ID.
          compact: If true, tool returns a reduced field set.
        """
        data = await self._request("GET", f"/tickets/{ticket_id}")
        return self._maybe_compact("ticket", data, compact)

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
    ) -> Json:
        """
        Create a ticket.

        Args:
          title: Ticket title.
          group: Group name (required).
          customer_id: Customer user ID. Use zammad_search_users() to find user ID.
          customer_email: Alternative to customer_id - email to create/identify customer.
          state: Ticket state name (e.g., "new", "open").
          priority: Priority name (e.g., "2 normal").
          owner_id: Agent owner user ID.
          article_body: Initial article/comment body (optional).
          article_type: Article type (default: "note"). Options: "note", "email", "phone", etc.
          article_internal: If true, article is internal (not visible to customer, default: True).
          compact: If true, tool returns a reduced field set.
        """
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

        data = await self._request("POST", "/tickets", json=payload)
        return self._maybe_compact("ticket", data, compact)

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
          customer_id: New customer user ID (or None to keep).
          organization_id: New organization ID (or None to keep).
          compact: If true, tool returns a reduced field set.
        """
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

        data = await self._request(
            "PUT",
            f"/tickets/{ticket_id}",
            json=payload if payload else None,
        )
        return self._maybe_compact("ticket", data, compact)

    # ----------------------------
    # Ticket Article Operations (Comments/Notes)
    # ----------------------------

    async def zammad_list_ticket_articles(
            self,
            ticket_id: TicketRef,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        List articles (comments/notes) for a ticket.

        Args:
          ticket_id: Ticket ID.
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set (still includes body).
        """
        data = await self._paginate(
            f"/ticket_articles/by_ticket/{ticket_id}",
            page=page,
            per_page=per_page,
        )
        return self._maybe_compact("article", data, compact)

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

        return await self._request("POST", "/ticket_articles", json=payload)

    # ----------------------------
    # User Operations
    # ----------------------------

    async def zammad_search_users(
            self,
            query: str,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        Search users by name, email, or login.

        Args:
          query: Search query string.
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        params = {"query": query}
        data = await self._paginate("/users/search", params=params, page=page, per_page=per_page)
        return self._maybe_compact("user", data, compact)

    async def zammad_get_user(
            self, user_id: int, compact: Optional[bool] = None
    ) -> Json:
        """
        Get a user by ID.

        Args:
          user_id: User ID.
          compact: If true, tool returns a reduced field set.
        """
        data = await self._request("GET", f"/users/{user_id}")
        return self._maybe_compact("user", data, compact)

    async def zammad_list_users(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        List all users.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        data = await self._paginate("/users", page=page, per_page=per_page)
        return self._maybe_compact("user", data, compact)

    # ----------------------------
    # Organization Operations
    # ----------------------------

    async def zammad_list_organizations(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        List organizations.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        data = await self._paginate("/organizations", page=page, per_page=per_page)
        return self._maybe_compact("organization", data, compact)

    async def zammad_get_organization(
            self, organization_id: int, compact: Optional[bool] = None
    ) -> Json:
        """
        Get an organization by ID.

        Args:
          organization_id: Organization ID.
          compact: If true, tool returns a reduced field set.
        """
        data = await self._request("GET", f"/organizations/{organization_id}")
        return self._maybe_compact("organization", data, compact)

    async def zammad_search_organizations(
            self,
            query: str,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        Search organizations by name.

        Args:
          query: Search query string.
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        params = {"query": query}
        data = await self._paginate("/organizations/search", params=params, page=page, per_page=per_page)
        return self._maybe_compact("organization", data, compact)

    # ----------------------------
    # Helper Lookup Endpoints
    # ----------------------------

    async def zammad_list_ticket_states(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        List ticket states.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        data = await self._paginate("/ticket_states", page=page, per_page=per_page)
        return self._maybe_compact("state", data, compact)

    async def zammad_list_groups(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        List groups.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        data = await self._paginate("/groups", page=page, per_page=per_page)
        return self._maybe_compact("group", data, compact)

    async def zammad_list_priorities(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        List ticket priorities.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        data = await self._paginate("/ticket_priorities", page=page, per_page=per_page)
        return self._maybe_compact("priority", data, compact)

    # ----------------------------
    # Report Profile Operations
    # ----------------------------

    async def zammad_list_report_profiles(
            self,
            page: int = 1,
            per_page: Optional[int] = None,
            compact: Optional[bool] = None,
    ) -> list[Json]:
        """
        List report profiles (created by admins). Requires 'report' permission.

        Args:
          page: Page number (1-based).
          per_page: Results per page (defaults to configured per_page).
          compact: If true, tool returns a reduced field set.
        """
        data = await self._paginate("/report_profiles", page=page, per_page=per_page)
        return self._maybe_compact("report_profile", data, compact)

    async def zammad_get_report_profile(
            self, report_profile_id: int, compact: Optional[bool] = None
    ) -> Json:
        """
        Get a report profile by ID. Requires 'report' permission.

        Args:
          report_profile_id: Report profile ID.
          compact: If true, tool returns a reduced field set.
        """
        data = await self._request("GET", f"/report_profiles/{report_profile_id}")
        return self._maybe_compact("report_profile", data, compact)
