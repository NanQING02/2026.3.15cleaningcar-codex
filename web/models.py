from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class FlowVector(BaseModel):
    start: List[float] = Field(..., min_length=2, max_length=2)
    end: List[float] = Field(..., min_length=2, max_length=2)


class ZonePayload(BaseModel):
    zone_a_detection: List[List[float]]
    zone_b_wash: List[List[float]]
    flow_vector: FlowVector


class ConfigPayload(BaseModel):
    system: Optional[Dict] = None
    video: Optional[Dict] = None
    logic: Optional[Dict] = None


class ConfigSelectPayload(BaseModel):
    name: str


class ConfigSaveAsPayload(BaseModel):
    name: str
    data: Dict
