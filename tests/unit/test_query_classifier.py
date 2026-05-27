"""BRANE-style query intent classifier tests."""

import pytest

from app.core.config import Settings
from app.models.chat import ChatRequest
from app.services.ai_service import QueryClassifier, QueryIntent


@pytest.fixture
def classifier() -> QueryClassifier:
    settings = Settings()
    return QueryClassifier(settings)


@pytest.mark.unit
def test_classify_greeting_hello(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="xin chao", project_id="test", tenant_id="default"))
    assert intent.type == "greeting"
    assert intent.complexity == 0


@pytest.mark.unit
def test_classify_greeting_hi(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="hi there", project_id="test", tenant_id="default"))
    assert intent.type == "greeting"


@pytest.mark.unit
def test_classify_coding_python(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="viet python code de sort array", project_id="test", tenant_id="default"))
    assert intent.type == "coding"
    assert intent.complexity == 2


@pytest.mark.unit
def test_classify_reasoning_analysis(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="phan tich chien luoc kinh doanh", project_id="test", tenant_id="default"))
    assert intent.type == "reasoning"
    assert intent.complexity == 2


@pytest.mark.unit
def test_classify_search_query(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="search google cho gia vang", project_id="test", tenant_id="default"))
    assert intent.type == "search"
    assert intent.requires_search is True


@pytest.mark.unit
def test_classify_rag_query_document(classifier: QueryClassifier) -> None:
    # "xem qua" prevents search match, "tai lieu" matches rag_query
    intent = classifier.classify(ChatRequest(user_message="xem qua tai lieu ve quy trinh xuat kho hang", project_id="test", tenant_id="default"))
    assert intent.type == "rag_query"
    assert intent.requires_rag is True


@pytest.mark.unit
def test_classify_factual_qa_price(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="gia vang hom nay bao nhieu", project_id="test", tenant_id="default"))
    assert intent.type == "factual_qa"
    assert intent.domain == "finance"


@pytest.mark.unit
def test_classify_casual_chat_short(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="khach hoi toi", project_id="test", tenant_id="default"))
    assert intent.type == "casual_chat"


@pytest.mark.unit
def test_classify_creative_writing(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="viet bai thơ ve mua xuan", project_id="test", tenant_id="default"))
    assert intent.type == "creative"


@pytest.mark.unit
def test_classify_vision_image_request(classifier: QueryClassifier) -> None:
    intent = classifier.classify(ChatRequest(user_message="anything", project_id="test", tenant_id="default", images=["data:image/png;base64,abc"]))
    assert intent.type == "casual_chat"
    assert intent.requires_vision is True


@pytest.mark.unit
def test_classify_default_unknown_long(classifier: QueryClassifier) -> None:
    # Long message >140 chars that matches no specific pattern
    long_msg = "toi muon biet nhieu hon ve viec hoc tieng anh moi ngay de co the giao tiep tot hon trong cong viec va cuoc song hang ngay lam viec nay rat kho khan va can nhieu thoi gian de hoan thanh mot cong viec tot nhat co the"
    intent = classifier.classify(ChatRequest(user_message=long_msg, project_id="test", tenant_id="default"))
    assert intent.type == "casual_chat"
    assert intent.complexity == 1  # > 140 chars


@pytest.mark.unit
def test_classify_complexity_coding_vs_casual(classifier: QueryClassifier) -> None:
    casual = classifier.classify(ChatRequest(user_message="hi", project_id="test", tenant_id="default"))
    coding = classifier.classify(ChatRequest(user_message="debug python traceback error", project_id="test", tenant_id="default"))
    assert casual.complexity == 0
    assert coding.complexity == 2


@pytest.mark.unit
def test_intent_routing_hint() -> None:
    intent = QueryIntent(type="coding", complexity=2)
    assert intent.routing_hint() == "coding"
