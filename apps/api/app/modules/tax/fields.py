"""Tax code fields for API shapes: a code is checked against the registry for its side (M1.10)."""

from typing import Annotated

from pydantic import AfterValidator, Field

from app.modules.tax.codes import Side, get


def _checker(side: Side) -> AfterValidator:
    def check(code: str) -> str:
        if get(side, code) is None:
            raise ValueError(f"unknown {side.value} tax code {code}")
        return code

    return AfterValidator(check)


SalesTaxCode = Annotated[str, Field(min_length=2, max_length=8, examples=["SR"]), _checker(Side.SALES)]
PurchaseTaxCode = Annotated[str, Field(min_length=2, max_length=8, examples=["SR"]), _checker(Side.PURCHASE)]
# Either side: checked by the service, which knows the document's side.
AnyTaxCode = Annotated[str, Field(min_length=2, max_length=8, examples=["SR"])]
