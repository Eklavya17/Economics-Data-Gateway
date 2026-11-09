"""Pydantic response models; each tool's output schema is one of these.

``series``: time-series tools (search, describe, latest, history).
``places``: areas, occupations, OEWS wages and QCEW area employment.
``compute``: real wage growth and series comparisons.
``reference``: bodies of the ``econ://`` resources (catalog, series, releases, status).
"""

from econ_gateway.models.compute import (
    CompareMeasure,
    CompareResponse,
    CompareRow,
    Deflator,
    RealWagePoint,
    RealWageResponse,
    WindowChange,
)
from econ_gateway.models.places import (
    AreaEmploymentResponse,
    AreaLevel,
    AreaMatch,
    EmploymentRow,
    FindAreaResponse,
    FindOccupationResponse,
    LausSeries,
    OccupationMatch,
    OccupationWagesResponse,
    WageMeasure,
    WageRow,
)
from econ_gateway.models.reference import (
    CatalogEntry,
    CatalogResponse,
    ReleaseCalendarResponse,
    SeriesResource,
    StatusResponse,
)
from econ_gateway.models.series import (
    DescribeResponse,
    FailedItem,
    GetLatestResponse,
    GetSeriesResponse,
    LatestValue,
    Observation,
    ReleaseInfo,
    ResponseFormat,
    SearchHit,
    SearchResponse,
    SeriesData,
    SeriesMeta,
    Source,
    Transform,
)

__all__ = [
    "CatalogEntry",
    "CatalogResponse",
    "ReleaseCalendarResponse",
    "ReleaseInfo",
    "SeriesResource",
    "StatusResponse",
    "CompareMeasure",
    "CompareResponse",
    "CompareRow",
    "Deflator",
    "RealWagePoint",
    "RealWageResponse",
    "WindowChange",
    "AreaEmploymentResponse",
    "AreaLevel",
    "AreaMatch",
    "DescribeResponse",
    "EmploymentRow",
    "FailedItem",
    "FindAreaResponse",
    "FindOccupationResponse",
    "GetLatestResponse",
    "GetSeriesResponse",
    "LatestValue",
    "LausSeries",
    "Observation",
    "OccupationMatch",
    "OccupationWagesResponse",
    "ResponseFormat",
    "SearchHit",
    "SearchResponse",
    "SeriesData",
    "SeriesMeta",
    "Source",
    "Transform",
    "WageMeasure",
    "WageRow",
]
