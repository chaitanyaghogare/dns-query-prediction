import numpy as np
from sklearn.tree import DecisionTreeClassifier

from dnscache.data.schema import Query
from dnscache.predict.features import FEATURES, FutureIndex, StatsTable
from dnscache.predict.models import TreePredictor, train_and_evaluate, tree_rules
from dnscache.predict.sequence import SequenceModel
from dnscache.sim.cache import Entry


def test_tree_predictor_matches_sklearn():
    rng = np.random.default_rng(0)
    X = rng.random((500, len(FEATURES)))
    y = (X[:, 0] + X[:, 3] > 1).astype(int)
    t = DecisionTreeClassifier(max_depth=4).fit(X, y)
    tp = TreePredictor(t)
    assert np.allclose([tp(x) for x in X], t.predict_proba(X)[:, 1])
    assert "class" in tree_rules(t)


def test_train_and_evaluate_reports_all_models():
    rng = np.random.default_rng(1)
    X = rng.random((400, len(FEATURES)))
    y = (X[:, 4] > 0.5).astype(int)
    _, rep = train_and_evaluate(X[:300], y[:300], X[300:], y[300:])
    assert set(rep) == {"decision_tree", "naive_bayes", "logistic_regression"}
    assert rep["decision_tree"]["f1"] > 0.9


def test_features_use_only_the_past():
    st = StatsTable()
    for t in (0, 30, 100):
        st.observe("a|A", t, "c1")
    e = Entry("a|A", 60, 100, 100, 160, hits=2)
    x = dict(zip(FEATURES, st.features("a|A", 158, e)))
    assert x["q_1m"] == 1 and x["q_5m"] == 3 and x["total_q"] == 3
    assert x["since_last_q"] == 58 and x["hits_this_life"] == 2 and x["mean_gap"] == 50


def test_future_index():
    f = FutureIndex([Query(5, "c", "a"), Query(50, "c", "a")])
    assert f.queried_between("a|A", 10, 60) and not f.queried_between("a|A", 51, 100)


def test_sequence_model_probabilities():
    qs = []
    for i in range(10):
        t = i * 100
        qs += [Query(t, "c1", "page.com"), Query(t + 1, "c1", "cdn.page.com")]
        if i < 4:
            qs.append(Query(t + 2, "c1", "ads.net"))
    qs.append(Query(2000, "c2", "cdn.page.com"))       # other client, unrelated
    m = SequenceModel(window_s=30, min_support=3).fit(sorted(qs, key=lambda q: q.ts))
    pred = dict(m.predict("page.com|A"))
    assert pred["cdn.page.com|A"] == 1.0 and abs(pred["ads.net|A"] - 0.4) < 1e-9
    assert m.predict("page.com|A", 0.7) == [("cdn.page.com|A", 1.0)]
    assert "4 of 10" in m.explain("page.com|A", "ads.net|A")


def test_sequence_model_roundtrip(tmp_path):
    qs = [Query(i * 100 + d, "c", n) for i in range(6) for d, n in ((0, "a"), (1, "b"))]
    m = SequenceModel(min_support=3).fit(qs)
    m.save(tmp_path / "s.json")
    assert SequenceModel.load(tmp_path / "s.json").predict("a|A") == m.predict("a|A")
