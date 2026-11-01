from __future__ import annotations

import pytest

from core import docid, report


pytestmark = pytest.mark.unit


def test_a_report_inherits_its_source_identity() -> None:
    source_id = docid.nouveau()
    meeting_id = docid.nouveau()
    project_id = docid.nouveau()

    fields = report._identity_fields({
        "id": source_id,
        "meeting_id": meeting_id,
        "project_id": project_id,
    })

    assert fields == {
        "source_document_id": source_id,
        "meeting_id": meeting_id,
        "project_id": project_id,
    }


@pytest.mark.parametrize("missing", ["id", "meeting_id", "project_id"])
def test_a_report_refuses_an_unmigrated_source(missing: str) -> None:
    source = {
        "id": docid.nouveau(),
        "meeting_id": docid.nouveau(),
        "project_id": docid.nouveau(),
    }
    source.pop(missing)

    with pytest.raises(ValueError, match="identité"):
        report._identity_fields(source)
