from pydantic import BaseModel, Field

class CreateAccountRequest(BaseModel):
    backend: str = Field(..., example="juwa")
    count: int   = Field(1, example=50)

class RechargeAccountRequest(BaseModel):
    backend: str    = Field(..., example="juwa")
    count: int      = Field(1, example=50)
    account_id: str = Field(..., example="abc123")