"""
Bridges MongoDB's ObjectId with Pydantic v2 so models can declare an
`id: PyObjectId` field that validates, serializes to str, and still
round-trips correctly with Motor.
"""
from typing import Any

from bson import ObjectId
from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema


class PyObjectId(str):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        def validate(value: Any) -> str:
            if isinstance(value, ObjectId):
                return str(value)
            if isinstance(value, str) and ObjectId.is_valid(value):
                return value
            raise ValueError("Invalid ObjectId")

        return core_schema.union_schema(
            [
                core_schema.is_instance_schema(ObjectId),
                core_schema.chain_schema(
                    [core_schema.str_schema(), core_schema.no_info_plain_validator_function(validate)]
                ),
            ],
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )


def to_object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise ValueError(f"Invalid ObjectId: {value}")
    return ObjectId(value)


def new_object_id_str() -> str:
    return str(ObjectId())
