
from app.models.content_plan import ContentPlan
from app.services.content_storage import ContentStorage
from tests.test_content_plan import valid_data


def test_save_and_retrieve_plan(tmp_path):
    storage = ContentStorage(
        db_path=tmp_path / "test.db",
        output_dir=tmp_path / "plans"
    )

    plan = ContentPlan.model_validate(
        valid_data()
    )

    plan_id, path = storage.save_plan(plan)

    assert path.exists()

    loaded = storage.get_plan(plan_id)

    assert loaded["title"] == plan.title
    assert loaded["review_status"] == "draft"
    assert len(loaded["scenes"]) == 2
