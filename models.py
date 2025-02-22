from datetime import datetime
import pydantic

class SensorData(pydantic.BaseModel):
    name: str
    device_id: str
    location: str
    value: float
    timestamp: datetime

class SensorBounds(pydantic.BaseModel):
    sensor: str
    min: float
    max: float

class Alert(pydantic.BaseModel):
    message: str = "Sensor data out of bounds"
    sensor_data: SensorData
    sensor_bounds: SensorBounds

class Memory(pydantic.BaseModel):
    message: str
    timestamp: datetime = pydantic.Field(default_factory=datetime.now)

class Notification(pydantic.BaseModel):
    title: str
    message: str
