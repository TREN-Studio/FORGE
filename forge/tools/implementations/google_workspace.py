"""
FORGE × Google Workspace Integration
====================================
Implements Google Docs, Google Sheets, and Gmail integrations.
"""

from __future__ import annotations

import pickle
import time
from pathlib import Path
from typing import Any

from forge.tools.base import ForgeTool, ToolResult

# Optional Google client imports with fallback
try:
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False


SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.send",
]

TOKEN_PATH = Path.home() / ".forge" / "google_token.pickle"
CREDS_PATH = Path.home() / ".forge" / "google_credentials.json"


def get_google_creds() -> Any | None:
    if not GOOGLE_LIBS_AVAILABLE:
        return None
    creds = None
    if TOKEN_PATH.exists():
        try:
            with open(TOKEN_PATH, "rb") as f:
                creds = pickle.load(f)
        except Exception:
            pass
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif CREDS_PATH.exists():
                flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                return None
            with open(TOKEN_PATH, "wb") as f:
                pickle.dump(creds, f)
        except Exception:
            return None
    return creds


class GoogleDocsTool(ForgeTool):
    name = "google-docs"
    description = "Read and write Google Docs documents"
    risk_class = "write"
    requires_auth = ["google_oauth"]
    available_actions = ["read", "create", "append"]

    async def execute(self, action: str, params: dict[str, Any]) -> ToolResult:
        if not GOOGLE_LIBS_AVAILABLE:
            return ToolResult(
                success=False,
                error="Google API client libraries are not installed. Run: pip install google-api-python-client google-auth-oauthlib",
            )

        creds = get_google_creds()
        if not creds:
            # Fallback mock for testing/demo when not fully authorized yet
            if params.get("mock") or params.get("demo"):
                return ToolResult(
                    success=True,
                    data="https://docs.google.com/document/d/mock-doc-id/edit",
                    action_taken=f"Mocked Google Doc creation for demo: {params.get('title')}",
                )
            return ToolResult(
                success=False,
                error="Google not connected. Put google_credentials.json in ~/.forge/ and run: forge tools connect google-docs",
            )

        try:
            service = build("docs", "v1", credentials=creds)

            if action == "read":
                doc_id = params.get("doc_id")
                if not doc_id:
                    return ToolResult(success=False, error="doc_id parameter is required")
                doc = service.documents().get(documentId=doc_id).execute()
                text = self._extract_text(doc.get("body", {}).get("content", []))
                return ToolResult(success=True, data=text, metadata={"title": doc.get("title")})

            elif action == "create":
                title = params.get("title", "FORGE Document")
                content = params.get("content", "")
                doc = service.documents().create(body={"title": title}).execute()
                doc_id = doc["documentId"]
                if content:
                    service.documents().batchUpdate(
                        documentId=doc_id,
                        body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
                    ).execute()
                url = f"https://docs.google.com/document/d/{doc_id}"
                return ToolResult(success=True, data=url, metadata={"doc_id": doc_id, "title": title})

            elif action == "append":
                doc_id = params.get("doc_id")
                content = params.get("content", "")
                if not doc_id or not content:
                    return ToolResult(success=False, error="doc_id and content parameters are required")
                doc = service.documents().get(documentId=doc_id).execute()
                end_index = doc["body"]["content"][-1]["endIndex"] - 1
                service.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": [{"insertText": {"location": {"index": end_index}, "text": "\n" + content}}]},
                ).execute()
                return ToolResult(success=True, data="Content appended successfully.")
            else:
                return ToolResult(success=False, error=f"Unsupported action: {action}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

    def _extract_text(self, content: list) -> str:
        text = []
        for element in content:
            if "paragraph" in element:
                for pe in element["paragraph"].get("elements", []):
                    if "textRun" in pe:
                        text.append(pe["textRun"]["content"])
        return "".join(text)


class GoogleSheetsTool(ForgeTool):
    name = "google-sheets"
    description = "Read and write Google Sheets spreadsheets"
    risk_class = "write"
    requires_auth = ["google_oauth"]
    available_actions = ["read", "write", "create"]

    async def execute(self, action: str, params: dict[str, Any]) -> ToolResult:
        if not GOOGLE_LIBS_AVAILABLE:
            return ToolResult(success=False, error="Google libraries not installed.")

        creds = get_google_creds()
        if not creds:
            if params.get("mock") or params.get("demo"):
                return ToolResult(
                    success=True,
                    data="https://docs.google.com/spreadsheets/d/mock-sheet-id/edit",
                    action_taken="Mocked Google Sheet creation.",
                )
            return ToolResult(success=False, error="Google not connected.")

        try:
            service = build("sheets", "v4", credentials=creds)

            if action == "read":
                ss_id = params.get("spreadsheet_id")
                range_name = params.get("range", "A1:Z1000")
                if not ss_id:
                    return ToolResult(success=False, error="spreadsheet_id is required")
                result = service.spreadsheets().values().get(
                    spreadsheetId=ss_id, range=range_name
                ).execute()
                return ToolResult(success=True, data=result.get("values", []))

            elif action == "write":
                ss_id = params.get("spreadsheet_id")
                range_name = params.get("range")
                values = params.get("values")
                if not ss_id or not range_name or not values:
                    return ToolResult(success=False, error="spreadsheet_id, range, and values are required")
                service.spreadsheets().values().update(
                    spreadsheetId=ss_id,
                    range=range_name,
                    valueInputOption="RAW",
                    body={"values": values},
                ).execute()
                return ToolResult(success=True, data="Written successfully")

            elif action == "create":
                title = params.get("title", "FORGE Sheet")
                data = params.get("data")
                spreadsheet = service.spreadsheets().create(
                    body={"properties": {"title": title}}
                ).execute()
                ss_id = spreadsheet["spreadsheetId"]
                url = f"https://docs.google.com/spreadsheets/d/{ss_id}"
                if data:
                    service.spreadsheets().values().update(
                        spreadsheetId=ss_id,
                        range="A1",
                        valueInputOption="RAW",
                        body={"values": data},
                    ).execute()
                return ToolResult(success=True, data=url, metadata={"spreadsheet_id": ss_id})
            else:
                return ToolResult(success=False, error=f"Unsupported action: {action}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class GmailTool(ForgeTool):
    name = "gmail"
    description = "Send emails via Gmail"
    risk_class = "publish"
    requires_auth = ["google_oauth"]
    available_actions = ["send"]

    async def execute(self, action: str, params: dict[str, Any]) -> ToolResult:
        if not GOOGLE_LIBS_AVAILABLE:
            return ToolResult(success=False, error="Google libraries not installed.")

        creds = get_google_creds()
        if not creds:
            if params.get("mock") or params.get("demo"):
                return ToolResult(
                    success=True,
                    data=f"Email sent (Mock) to {params.get('to')}",
                    action_taken=f"Mocked sending email to {params.get('to')}",
                )
            return ToolResult(success=False, error="Google not connected.")

        try:
            service = build("gmail", "v1", credentials=creds)

            if action == "send":
                to = params.get("to")
                subject = params.get("subject", "FORGE Automated Mail")
                body = params.get("body", "")
                if not to or not body:
                    return ToolResult(success=False, error="to and body are required")

                import base64
                from email.mime.text import MIMEText
                message = MIMEText(body)
                message["to"] = to
                message["subject"] = subject
                raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
                service.users().messages().send(userId="me", body={"raw": raw}).execute()
                return ToolResult(success=True, data=f"Email sent to {to}", action_taken=f"Sent email to {to}")
            else:
                return ToolResult(success=False, error=f"Unsupported action: {action}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
