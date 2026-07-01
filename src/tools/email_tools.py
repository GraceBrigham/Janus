"""
Email tools implementation providing list, search, get, send, and delete functionality.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any

from .data_store import get_collection

emails: Optional[List[Dict]] = None


def _get_emails() -> List[Dict]:
    global emails
    if emails is None:
        emails = get_collection("emails")
    return emails


def _is_unread(email: Dict[str, Any]) -> bool:
    # Treat missing read flag as unread for backward compatibility.
    return not email.get("read", False)


def _matches_sender(sender_filter: str, sender: str) -> bool:
    if not sender_filter:
        return True
    sender_filter = sender_filter.lower()
    sender = sender.lower()
    return sender_filter in sender


def list_emails(unread: bool = False) -> str:
    """
    List emails, optionally filtering for unread messages.

    Args:
        unread: If true, only return unread messages.

    Returns:
        A formatted list of matching emails.
    """
    global emails
    emails = _get_emails()

    if not emails:
        return "No emails found."

    filtered_emails = emails
    if unread:
        filtered_emails = [email for email in emails if _is_unread(email)]

    if not filtered_emails:
        return "No unread emails found." if unread else "No emails found."

    result = f"Found {len(filtered_emails)} email(s):\n\n"
    for email in reversed(filtered_emails):
        result += f"Email ID: {email['id']}\n"
        result += f"From: {email['from']}\n"
        result += f"To: {email['to']}\n"
        result += f"Subject: {email['subject']}\n"
        result += f"Sent: {email['timestamp']}\n"
        result += f"Body: {email['body']}\n"
        result += "-" * 40 + "\n\n"

    return result.rstrip()


def search_emails(**kwargs: Any) -> str:
    """
    Search emails by sender.

    Args:
        from: Sender filter (substring match).

    Returns:
        A formatted list of matching emails.
    """
    sender_filter = kwargs.get("from", "")

    global emails
    emails = _get_emails()

    if not emails:
        return "No emails found."

    filtered_emails = [
        email for email in emails if _matches_sender(sender_filter, email.get("from", ""))
    ]

    if not filtered_emails:
        return f"No emails found from '{sender_filter}'."

    result = f"Found {len(filtered_emails)} email(s):\n\n"
    for email in reversed(filtered_emails):
        result += f"Email ID: {email['id']}\n"
        result += f"From: {email['from']}\n"
        result += f"To: {email['to']}\n"
        result += f"Subject: {email['subject']}\n"
        result += f"Sent: {email['timestamp']}\n"
        result += f"Body: {email['body']}\n"
        result += "-" * 40 + "\n\n"

    return result.rstrip()


def get_email(**kwargs: Any) -> str:
    """
    Get a specific email by id or sender.

    Args:
        id: Email identifier.
        from: Sender filter (substring match).

    Returns:
        A formatted email if found.
    """
    email_id = kwargs.get("id")
    sender_filter = kwargs.get("from", "")

    global emails
    emails = _get_emails()

    if not emails:
        return "No emails found."

    match: Optional[Dict[str, Any]] = None
    if email_id is not None:
        match = next((email for email in emails if str(email["id"]) == str(email_id)), None)
    elif sender_filter:
        matching = [
            email for email in emails if _matches_sender(sender_filter, email.get("from", ""))
        ]
        if matching:
            match = matching[-1]
    else:
        return "Provide either id or from to get an email."

    if not match:
        return "Email not found."

    match["read"] = True

    result = (
        "Email details:\n\n"
        f"Email ID: {match['id']}\n"
        f"From: {match['from']}\n"
        f"To: {match['to']}\n"
        f"Subject: {match['subject']}\n"
        f"Sent: {match['timestamp']}\n"
        f"Body: {match['body']}"
    )
    return result


def send_email(to: str, subject: str, body: str, attachments: Optional[List[Any]] = None) -> str:
    """
    Send an email.

    Args:
        to: Recipient email address
        subject: Email subject line
        body: Email body content
        attachments: Optional list of attachments

    Returns:
        Confirmation message about the sent email.
    """
    global emails
    emails = _get_emails()

    next_id = max([email["id"] for email in emails], default=0) + 1
    email = {
        "id": next_id,
        "from": "user@example.com",
        "to": to,
        "subject": subject,
        "body": body,
        "timestamp": datetime.now().isoformat(),
        "status": "sent",
        "read": True,
        "attachments": attachments or []
    }

    emails.append(email)

    attachment_note = ""
    if attachments:
        attachment_note = f"\nAttachments: {len(attachments)} file(s)"

    return (
        "Email sent successfully.\n\n"
        f"Email ID: {email['id']}\n"
        f"From: {email['from']}\n"
        f"To: {email['to']}\n"
        f"Subject: {email['subject']}\n"
        f"Sent at: {email['timestamp']}"
        f"{attachment_note}"
        "\n\n"
        f"Message body:\n{body}"
    )


def delete_email(id: str) -> str:
    """
    Delete an email by id.

    Args:
        id: Email identifier.

    Returns:
        Confirmation message about the deletion.
    """
    global emails
    emails = _get_emails()

    match = next((email for email in emails if str(email["id"]) == str(id)), None)
    if not match:
        return f"Email with ID {id} not found."

    emails[:] = [email for email in emails if str(email["id"]) != str(id)]
    return f"Email with ID {id} deleted."
