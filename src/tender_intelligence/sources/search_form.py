""":mod:`tender_intelligence.sources.search_form` — Search-form-driven source strategy.

docs/05 §5.1 names "Search-form-driven" as a source type for a site whose tender list is only
reachable by *using* a form::

    GET the search page → populate fields → submit → parse the results

The site name is not in this module and may not be added: which form, which fields, which
values, GET or POST, and which element to treat as the result set are all ``parser_config``
facts (PROJECT_RULES #10).

**The execution boundary.** A form flow is a *mechanism*, not a parsing problem: a plain HTML
form needs only an HTTP client, while a form whose results only appear after JavaScript
executes needs a browser. The repository has no browser dependency today, and adding one is
not justified by an adapter contract, so this module defines the seam and ships the one
implementation the current dependency set can honestly support:

* :class:`FormDriver` — the protocol. ``run(spec) -> HttpResponse`` is the whole contract, so
  a Playwright- or Selenium-backed driver can be added later without changing this adapter,
  its configuration, or anything downstream of it.
* :class:`HttpFormDriver` — the default. GET the form page, read the form's own controls with
  their current values, merge the configured overrides, submit. Every request goes through a
  :class:`~tender_intelligence.sources.polite.Requester`, so robots.txt, pacing, timeout,
  retry and backoff are the same ones the other strategies obey.
* :class:`UnavailableFormDriver` — the honest failure for a source that declares it needs a
  browser. It raises a structured ``source_not_runnable`` error rather than submitting a form
  that cannot work, because a browser-driven form submitted as a plain request returns a page
  with no results — which is indistinguishable from a source that simply has no tenders.

Everything after the submit is inherited from the paginated strategy: the result page is an
ordinary HTML list, so its rows, next-page links, detail pages and attachments are parsed by
exactly the same tested code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from bs4 import BeautifulSoup, Tag

from tender_intelligence.core.correlation import get_correlation_id
from tender_intelligence.core.errors import PARSER_MISMATCH, SOURCE_NOT_RUNNABLE, SOURCE_UNREACHABLE
from tender_intelligence.interfaces.source import SourceError, SourceType, TenderListing
from tender_intelligence.sources.normalize import absolute_url, apply_query_params, clean_text
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import (
    Fetcher,
    HttpResponse,
    PoliteHttpClient,
    Requester,
    requester_get,
)
from tender_intelligence.sources.waho import PaginatedHtmlAdapter

log = logging.getLogger("tender_intelligence.sources.search_form")

#: Default ``<form>`` selector: the page's first form. Configuration should name the form
#: explicitly wherever a page carries more than one (search, login, newsletter, ...).
DEFAULT_FORM_SELECTOR = "form"

#: Control types whose value is implied by being present rather than by a ``value``.
_PRESENCE_CONTROL_TYPES = frozenset({"checkbox", "radio"})

#: Control types a browser never submits as a name/value pair.
_NON_SUBMITTING_INPUT_TYPES = frozenset({"submit", "button", "image", "reset", "file"})


@dataclass(frozen=True)
class FormSpec:
    """One configured form flow, resolved from ``parser_config["form"]``."""

    form_url: str
    form_selector: str = DEFAULT_FORM_SELECTOR
    method: str = "GET"
    fields: dict[str, str] = field(default_factory=dict)
    submit_selector: str | None = None
    requires_browser: bool = False
    #: Extra query parameters merged into the submit URL, for sites that expose filters as
    #: links as well as form fields. They belong to the flow, not to result parsing.
    base_params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, form_url: str, config: dict[str, Any] | None) -> FormSpec:
        """Build a spec from the adapter's merged ``parser_config``.

        The flow is read from the ``"form"`` section; ``query_params`` (the same key the
        paginated strategy uses) is folded into ``base_params`` so a form-driven source can
        still pin a facet or a page size.
        """
        settings = config or {}
        section = settings.get("form")
        section = section if isinstance(section, dict) else {}
        raw_fields = section.get("fields")
        raw_params = settings.get("query_params")
        return cls(
            form_url=form_url,
            form_selector=clean_text(section.get("form_selector")) or DEFAULT_FORM_SELECTOR,
            method=(clean_text(section.get("method")) or "GET").upper(),
            fields={
                str(key): "" if value is None else str(value)
                for key, value in (raw_fields or {}).items()
            },
            submit_selector=clean_text(section.get("submit_selector")) or None,
            requires_browser=bool(section.get("requires_browser", False)),
            base_params={str(key): value for key, value in (raw_params or {}).items()},
        )


class FormDriver(Protocol):
    """Executes one search-form flow and returns the response holding the results."""

    def run(self, spec: FormSpec) -> HttpResponse:
        ...


class HttpFormDriver:
    """Default :class:`FormDriver`: perform the flow with plain HTTP requests.

    The page's own form supplies the defaults — its ``action``, its ``method``, every named
    input/select/textarea and its current value — so a site that rotates a hidden
    anti-forgery token or hides a filter set in the markup keeps working without
    configuration changes. Configured ``fields`` are merged last and win.
    """

    def __init__(self, *, policy: CrawlPolicy | None = None, requester: Requester | None = None):
        self._policy = policy or CrawlPolicy()
        self._requester = requester

    def run(self, spec: FormSpec) -> HttpResponse:
        correlation_id = get_correlation_id()
        page = self._send("GET", spec.form_url)
        _require_ok(page, spec.form_url)
        form = _select_form(page.text, spec.form_selector, page.final_url)
        action = apply_query_params(
            absolute_url(page.final_url, _action_of(form, spec.submit_selector)), spec.base_params
        )
        values = _form_values(form)
        values.update(spec.fields)

        log.info(
            "submitting search form: %s %s with %d field(s)",
            spec.method,
            action,
            len(values),
            extra={"stage": "discovery", "status": "fetch", "correlation_id": correlation_id},
        )
        if spec.method == "POST":
            response = self._send("POST", action, data=values)
        else:
            response = self._send("GET", apply_query_params(action, values))
        return _require_ok(response, action)

    def _send(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        if self._requester is not None:
            return self._requester(method, url, **kwargs)
        client = PoliteHttpClient.build(self._policy)
        try:
            return client(method, url, **kwargs)
        finally:
            client.close()


class UnavailableFormDriver:
    """A :class:`FormDriver` that cannot run, and says so with a structured error.

    Selected when a source declares ``form.requires_browser`` and no browser-capable driver
    was supplied. Failing loudly is the point — see this module's docstring.
    """

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def run(self, spec: FormSpec) -> HttpResponse:
        raise SourceError(
            f"search form at {spec.form_url} cannot be executed: {self._reason}",
            error_code=SOURCE_NOT_RUNNABLE,
            context={"form_url": spec.form_url, "reason": "form_driver_unavailable"},
        )


class SearchFormAdapter(PaginatedHtmlAdapter):
    """Adapter for a source whose results are reached by submitting a search form.

    Only the *entry* request differs from a paginated source: the configured form is executed
    to obtain the result page, and every subsequent request — the next-page links that result
    page advertises, and the detail pages downstream — is an ordinary polite HTTP request,
    because a site that accepted a form submission accepts the URLs it returned.
    """

    source_type: str = SourceType.SEARCH_FORM.value

    def __init__(
        self,
        listing_url: str,
        *,
        base_url: str | None = None,
        parser_config: dict[str, Any] | None = None,
        policy: CrawlPolicy | None = None,
        fetcher: Fetcher | None = None,
        requester: Requester | None = None,
        form_driver: FormDriver | None = None,
    ) -> None:
        injected_fetcher = fetcher or (requester_get(requester) if requester is not None else None)
        super().__init__(
            listing_url,
            base_url=base_url,
            parser_config=parser_config,
            policy=policy,
            # An injected Requester serves the *whole* crawl, not only the submit: the
            # next-page and detail requests that follow a form submission are ordinary GETs
            # and must stay on the same injected transport.
            fetcher=injected_fetcher,
        )
        self._form_spec = FormSpec.from_config(listing_url, self._config)
        self._entry_url = self._start_url()
        self._form_driver: FormDriver = form_driver or self._default_driver(requester)
        self._form_used = False

    def _default_driver(self, requester: Requester | None) -> FormDriver:
        # A Requester is still not a browser: no supplied requester can make a form that
        # needs JavaScript work, and submitting it anyway returns an empty result page
        # rather than an error. So this decision depends only on what the source declares.
        if self._form_spec.requires_browser:
            return UnavailableFormDriver(
                "no browser-capable form driver is installed; supply one explicitly"
            )
        return HttpFormDriver(policy=self._policy, requester=requester)

    def list_new_tenders(self) -> list[TenderListing]:
        """Execute the configured form, then parse and paginate the result page."""
        self._form_used = False
        return super().list_new_tenders()

    def _fetch(self, url: str) -> HttpResponse:
        # The first request is the one the crawl derives from the configured listing URL
        # (including its ``query_params``); everything after it is a link the site returned.
        if not self._form_used and url in (self._listing_url, self._entry_url):
            self._form_used = True
            return self._form_driver.run(self._form_spec)
        return super()._fetch(url)

    @property
    def form_spec(self) -> FormSpec:
        """The resolved flow, for logging and for a source test to report on."""
        return self._form_spec


# -- form parsing helpers ---------------------------------------------------


def _select_form(html: str, selector: str, page_url: str) -> Tag:
    """Return the configured form, or fail with ``parser_mismatch``."""
    form = BeautifulSoup(html, "html.parser").select_one(selector)
    if form is None:
        raise SourceError(
            "search page has no form matching the configured selector",
            error_code=PARSER_MISMATCH,
            context={"url": page_url, "form_selector": selector},
        )
    return form


def _action_of(form: Tag, submit_selector: str | None) -> str:
    """The form's target URL, from ``action`` or from a ``formaction`` submit control."""
    action = str(form.get("action") or "").strip()
    if action or not submit_selector:
        return action
    control = form.select_one(submit_selector)
    if control is None:
        return ""
    return str(control.get("formaction") or "").strip()


def _form_values(form: Tag) -> dict[str, str]:
    """Current values of every named control a browser would submit with this form.

    ``disabled`` controls are skipped, checkboxes and radios contribute only when checked,
    ``select`` contributes its selected option (or the first one when none is marked), and a
    text input with no ``value`` attribute contributes an empty string — the same rules a
    browser applies, so a hidden token or a default filter arrives with the submission.
    """
    values: dict[str, str] = {}
    for control in form.select("input, select, textarea"):
        name = str(control.get("name") or "").strip()
        if not name or control.has_attr("disabled"):
            continue
        tag = control.name.lower()
        if tag == "input":
            input_type = str(control.get("type") or "text").strip().lower()
            if input_type in _NON_SUBMITTING_INPUT_TYPES:
                continue
            if input_type in _PRESENCE_CONTROL_TYPES:
                if control.has_attr("checked"):
                    values[name] = str(control.get("value") or "on")
                continue
            values[name] = str(control.get("value") or "")
        elif tag == "textarea":
            values[name] = str(control.get_text())
        else:  # select
            options = control.select("option")
            chosen = next((opt for opt in options if opt.has_attr("selected")), None)
            if chosen is None:
                chosen = options[0] if options else None
            if chosen is not None:
                values[name] = str(chosen.get("value") or chosen.get_text(" ", strip=True))
    return values


def _require_ok(response: HttpResponse, url: str) -> HttpResponse:
    if response.status_code >= 400:
        raise SourceError(
            f"source unreachable: {url} (http {response.status_code})",
            error_code=SOURCE_UNREACHABLE,
            context={"url": url, "status_code": response.status_code},
        )
    return response
