"""Unit tests for the attachment planner (v1.1 §5.7 provider-capability-aware behaviour)."""

from __future__ import annotations

from tender_intelligence.mail.message import MailAttachment
from tender_intelligence.mail.planner import AttachmentPlanner
from tender_intelligence.mail.provider import Capabilities

MB = 1024 * 1024


def _doc(name: str, size_mb: float = 1) -> MailAttachment:
    return MailAttachment(
        filename=name, content=b"x" * int(size_mb * MB), content_type="application/pdf"
    )


class TestAttachmentPlanner:
    def test_no_limits_attaches_all(self):
        plan = AttachmentPlanner(Capabilities()).plan([_doc("a.pdf", 2), _doc("b.pdf", 3)])
        assert len(plan.attach) == 2
        assert plan.link_filenames == ()
        assert plan.total_attached_bytes == 5 * MB
        assert not plan.over_message_limit

    def test_count_limit(self):
        plan = AttachmentPlanner(Capabilities(max_attachments=2)).plan(
            [_doc("a.pdf"), _doc("b.pdf"), _doc("c.pdf")]
        )
        assert [a.filename for a in plan.attach] == ["a.pdf", "b.pdf"]
        assert plan.link_filenames == ("c.pdf",)

    def test_per_file_size_limit(self):
        plan = AttachmentPlanner(Capabilities(max_attachment_mb=1)).plan(
            [_doc("small.pdf", 0.5), _doc("big.pdf", 5)]
        )
        assert [a.filename for a in plan.attach] == ["small.pdf"]
        assert plan.link_filenames == ("big.pdf",)

    def test_message_size_limit(self):
        plan = AttachmentPlanner(Capabilities(max_message_mb=3)).plan(
            [_doc("a.pdf", 2), _doc("b.pdf", 2)]
        )
        # second won't fit in remaining 1MB -> goes to links
        assert [a.filename for a in plan.attach] == ["a.pdf"]
        assert plan.link_filenames == ("b.pdf",)

    def test_typical_provider(self):
        plan = AttachmentPlanner(
            Capabilities(max_attachments=20, max_attachment_mb=10, max_message_mb=25)
        ).plan([_doc(f"doc{i}.pdf", 1) for i in range(30)])
        assert len(plan.attach) == 20
        assert len(plan.link_filenames) == 10

    def test_sendlib_free_tier(self):
        plan = AttachmentPlanner(Capabilities(max_attachments=5, max_attachment_mb=1)).plan(
            [
                _doc("a.pdf", 0.5),
                _doc("b.pdf", 0.5),
                _doc("c.pdf", 0.5),
                _doc("d.pdf", 0.5),
                _doc("e.pdf", 0.5),
                _doc("f.pdf", 0.2),
            ]
        )
        assert len(plan.attach) == 5
        assert len(plan.link_filenames) == 1

    def test_email_message_validation(self):
        from tender_intelligence.mail.message import EmailMessage, InvalidAddress

        EmailMessage(to=("a@b.co",), subject="x").validate()
        try:
            EmailMessage(to=("not-an-email",), subject="x").validate()
        except InvalidAddress:
            pass
        else:
            raise AssertionError("expected InvalidAddress")
