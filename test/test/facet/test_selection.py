"""
Tests for module facet.selection
"""

import logging
from typing import List

import numpy as np
import pandas as pd
import pytest
from scipy.stats import loguniform, randint, zipfian
from sklearn import datasets

from pytools.expression import freeze
from pytools.expression.atomic import Id
from sklearndf import TransformerDF
from sklearndf.classification import SVCDF
from sklearndf.pipeline import ClassifierPipelineDF, PipelineDF, RegressorPipelineDF
from sklearndf.regression import (
    AdaBoostRegressorDF,
    LinearRegressionDF,
    RandomForestRegressorDF,
)
from sklearndf.regression.extra import LGBMRegressorDF

from ..conftest import check_ranking
from facet.crossfit import LearnerCrossfit
from facet.data import Sample
from facet.selection import (
    LearnerEvaluation,
    LearnerGrid,
    LearnerRanker,
    MultiClassifierParameterSpace,
    MultiRegressorParameterSpace,
    ParameterSpace,
)
from facet.validation import BootstrapCV

log = logging.getLogger(__name__)


def test_parameter_grid() -> None:

    grid = LearnerGrid(
        pipeline=ClassifierPipelineDF(classifier=SVCDF(gamma="scale")),
        learner_parameters={"a": [1, 2, 3], "b": [11, 12], "c": [21, 22]},
    )

    grid_expected = [
        {"classifier__a": 1, "classifier__b": 11, "classifier__c": 21},
        {"classifier__a": 2, "classifier__b": 11, "classifier__c": 21},
        {"classifier__a": 3, "classifier__b": 11, "classifier__c": 21},
        {"classifier__a": 1, "classifier__b": 12, "classifier__c": 21},
        {"classifier__a": 2, "classifier__b": 12, "classifier__c": 21},
        {"classifier__a": 3, "classifier__b": 12, "classifier__c": 21},
        {"classifier__a": 1, "classifier__b": 11, "classifier__c": 22},
        {"classifier__a": 2, "classifier__b": 11, "classifier__c": 22},
        {"classifier__a": 3, "classifier__b": 11, "classifier__c": 22},
        {"classifier__a": 1, "classifier__b": 12, "classifier__c": 22},
        {"classifier__a": 2, "classifier__b": 12, "classifier__c": 22},
        {"classifier__a": 3, "classifier__b": 12, "classifier__c": 22},
    ]

    _len = len(grid_expected)

    # length of the grid
    assert len(grid) == _len

    # iterating all items in the grid
    for item, expected in zip(grid, grid_expected):
        assert item == expected

    # positive indices
    for i in range(_len):
        assert grid[i] == grid_expected[i]

    # negative indices
    for i in range(-_len, 0):
        assert grid[i] == grid_expected[_len + i]

    # exceptions raised for out-of-bounds indices
    with pytest.raises(expected_exception=ValueError):
        _ = grid[_len]
        _ = grid[-_len - 1]

    # slicing support
    assert grid[-10:10:2] == grid_expected[-10:10:2]


def test_model_ranker(
    regressor_grids: List[LearnerGrid[RegressorPipelineDF]], sample: Sample, n_jobs: int
) -> None:

    expected_scores = [0.745, 0.742, 0.7, 0.689, 0.675, 0.675, 0.61, 0.61, 0.61, 0.61]
    expected_learners = [
        RandomForestRegressorDF,
        RandomForestRegressorDF,
        AdaBoostRegressorDF,
        AdaBoostRegressorDF,
        LinearRegressionDF,
        LinearRegressionDF,
        LGBMRegressorDF,
        LGBMRegressorDF,
        LGBMRegressorDF,
        LGBMRegressorDF,
    ]
    expected_parameters = {
        0: dict(regressor__n_estimators=80, regressor__random_state=42),
        1: dict(regressor__n_estimators=50, regressor__random_state=42),
        2: dict(regressor__n_estimators=50, regressor__random_state=42),
        3: dict(regressor__n_estimators=80, regressor__random_state=42),
    }

    # define the circular cross validator with just 5 splits (to speed up testing)
    cv = BootstrapCV(n_splits=5, random_state=42)

    ranker: LearnerRanker[RegressorPipelineDF] = LearnerRanker(
        grids=regressor_grids, cv=cv, scoring="r2", n_jobs=n_jobs
    ).fit(sample=sample)

    log.debug(f"\n{ranker.summary_report()}")

    assert isinstance(ranker.best_model_crossfit_, LearnerCrossfit)

    ranking = ranker.ranking_

    assert len(ranking) > 0
    assert isinstance(ranking[0], LearnerEvaluation)
    assert all(
        ranking_hi.ranking_score >= ranking_lo.ranking_score
        for ranking_hi, ranking_lo in zip(ranking, ranking[1:])
    )

    # check if parameters set for estimators actually match expected:
    for evaluation in ranker.ranking_:
        pipeline_parameters = evaluation.pipeline.get_params()
        for name, value in evaluation.parameters.items():
            assert (
                name in pipeline_parameters
            ), f"parameter {name} is a parameter in evaluation.pipeline"
            assert (
                pipeline_parameters[name] == value
            ), f"evaluation.pipeline.{name} is set to {value}"

    check_ranking(
        ranking=ranker.ranking_,
        expected_scores=expected_scores,
        expected_learners=expected_learners,
        expected_parameters=expected_parameters,
    )


def test_model_ranker_no_preprocessing(n_jobs) -> None:

    expected_learner_scores = [0.943, 0.913, 0.913, 0.884]

    # define a yield-engine circular CV:
    cv = BootstrapCV(n_splits=5, random_state=42)

    # define parameters and pipeline
    models = [
        LearnerGrid(
            pipeline=ClassifierPipelineDF(
                classifier=SVCDF(gamma="scale"), preprocessing=None
            ),
            learner_parameters={"kernel": ["linear", "rbf"], "C": [1, 10]},
        )
    ]

    #  load scikit-learn test-data and convert to pd
    iris = datasets.load_iris()
    test_data = pd.DataFrame(
        data=np.c_[iris["data"], iris["target"]],
        columns=[*iris["feature_names"], "target"],
    )
    test_sample: Sample = Sample(observations=test_data, target_name="target")

    model_ranker: LearnerRanker[ClassifierPipelineDF[SVCDF]] = LearnerRanker(
        grids=models, cv=cv, n_jobs=n_jobs
    ).fit(sample=test_sample)

    log.debug(f"\n{model_ranker.summary_report()}")

    check_ranking(
        ranking=model_ranker.ranking_,
        expected_scores=expected_learner_scores,
        expected_learners=[SVCDF] * 4,
        expected_parameters={
            0: dict(classifier__C=10, classifier__kernel="linear"),
            3: dict(classifier__C=1, classifier__kernel="rbf"),
        },
    )

    assert (
        model_ranker.ranking_[0].ranking_score >= 0.8
    ), "expected a best performance of at least 0.8"


def test_parameter_space(
    sample: Sample, simple_preprocessor: TransformerDF, n_jobs: int
) -> None:

    # distributions

    randint_3_10 = randint(3, 10)
    loguniform_0_01_0_10 = loguniform(0.01, 0.1)
    loguniform_0_05_0_10 = loguniform(0.05, 0.1)
    zipfian_1_32 = zipfian(1.0, 32)

    # parameter space 1

    pipeline_1 = RegressorPipelineDF(
        regressor=RandomForestRegressorDF(random_state=42),
        preprocessing=simple_preprocessor,
    )
    ps_1 = ParameterSpace(pipeline_1)
    ps_1.regressor.min_weight_fraction_leaf = loguniform_0_01_0_10
    ps_1.regressor.max_depth = randint_3_10
    ps_1.regressor.min_samples_leaf = loguniform_0_05_0_10

    with pytest.raises(
        AttributeError,
        match=r"^unknown parameter name for RandomForestRegressorDF: unknown$",
    ):
        ps_1.regressor.unknown = 1

    with pytest.raises(
        TypeError,
        match=(
            "^expected list or distribution for parameter min_samples_leaf "
            "but got: 1$"
        ),
    ):
        ps_1.regressor.min_samples_leaf = 1

    # parameter space 2

    pipeline_2 = RegressorPipelineDF(
        regressor=LGBMRegressorDF(random_state=42),
        preprocessing=simple_preprocessor,
    )
    ps_2 = ParameterSpace(pipeline_2)
    ps_2.regressor.max_depth = randint_3_10
    ps_2.regressor.min_child_samples = zipfian_1_32

    # multi parameter space

    with pytest.raises(
        TypeError,
        match=(
            r"^arg estimator_type must be a subclass of ClassifierPipelineDF but is: "
            r"RegressorPipelineDF$"
        ),
    ):
        # noinspection PyTypeChecker
        MultiClassifierParameterSpace(ps_1, ps_2, estimator_type=RegressorPipelineDF)

    with pytest.raises(
        TypeError,
        match=(
            r"^all candidate estimators must be instances of ClassifierPipelineDF, "
            r"but candidate estimators include: RegressorPipelineDF$"
        ),
    ):
        # noinspection PyTypeChecker
        MultiClassifierParameterSpace(ps_1, ps_2)

    mps = MultiRegressorParameterSpace(ps_1, ps_2)

    # test

    assert freeze(mps.to_expression()) == freeze(
        Id.MultiRegressorParameterSpace(
            Id.PipelineDF(steps=[("candidate", pipeline_1.to_expression())]),
            [
                Id.ParameterSpace(
                    candidate=pipeline_1.to_expression(),
                    **{
                        "candidate.regressor.min_weight_fraction_leaf": (
                            Id.loguniform(0.01, 0.1)
                        ),
                        "candidate.regressor.max_depth": Id.randint(3, 10),
                        "candidate.regressor.min_samples_leaf": (
                            Id.loguniform(0.05, 0.1)
                        ),
                    },
                ),
                Id.ParameterSpace(
                    candidate=pipeline_2.to_expression(),
                    **{
                        "candidate.regressor.max_depth": Id.randint(3, 10),
                        "candidate.regressor.min_child_samples": Id.zipfian(1.0, 32),
                    },
                ),
            ],
        )
    )

    assert type(mps.estimator) == PipelineDF
    assert mps.estimator.steps == [("candidate", pipeline_1)]

    assert mps.parameters == [
        {
            "candidate": [pipeline_1],
            "candidate__regressor__max_depth": randint_3_10,
            "candidate__regressor__min_samples_leaf": loguniform_0_05_0_10,
            "candidate__regressor__min_weight_fraction_leaf": loguniform_0_01_0_10,
        },
        {
            "candidate": [pipeline_2],
            "candidate__regressor__max_depth": randint_3_10,
            "candidate__regressor__min_child_samples": zipfian_1_32,
        },
    ]
