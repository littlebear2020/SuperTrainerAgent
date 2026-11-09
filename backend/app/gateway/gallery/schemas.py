"""Pydantic schemas for Recipe Card and Showcase data models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RecipeStepVar(BaseModel):
    """Configuration for a single template variable in a recipe step."""

    label: str = Field(..., description="Display label for the form field")
    # 'file' 与顶层 VarConfig（routers/gallery.py）对齐——提取 prompt 规则 7 会让
    # LLM 对"依赖上传文件"的方法论产出 type='file' 变量，缺它 extract-recipe 422。
    type: Literal["string", "select", "file"] = Field("string", description="Field type: 'string', 'select' or 'file'")
    required: bool = Field(False, description="Whether this field is required")
    placeholder: str | None = Field(None, description="Placeholder text")
    options: list[str] | None = Field(None, description="Options for 'select' type")
    accept: str | None = Field(None, description="File type filter for 'file' type, e.g. '.pdf,.docx'")
    multiple: bool = Field(False, description="Allow multiple files ('file' type)")


class RecipeStep(BaseModel):
    """A single step in a creation recipe."""

    instruction: str = Field(..., description="What this step does (shown to user)")
    template: str = Field(..., description="Prompt template with {variable} placeholders")
    example: str | None = Field(None, description="Concrete example from original conversation")
    tip: str | None = Field(None, description="Why this approach works (educational)")
    vars: dict[str, RecipeStepVar] | None = Field(None, description="Form field configuration")


class ShowcaseItem(BaseModel):
    """A single showcase media item."""

    url: str = Field(..., description="URL to the showcase media")
    caption: str | None = Field(None, description="Caption for the media")


class ShowcaseConfig(BaseModel):
    """Configuration for displaying case results."""

    type: Literal["image", "gallery", "video", "document"] = Field("image", description="Media type")
    items: list[ShowcaseItem] = Field(..., description="List of showcase items")


class Recipe(BaseModel):
    """A complete creation recipe distilled from a conversation."""

    title: str = Field(..., description="Recipe title")
    goal: str = Field(..., description="One-line goal description")
    approach: str | None = Field(None, description="Methodology name")
    difficulty: Literal["beginner", "intermediate", "advanced"] = Field("intermediate", description="Difficulty level")
    est_steps: int = Field(3, description="Expected interaction rounds")
    context_blocks: list[str] = Field(default_factory=list, description="AI context instructions for system prompt")
    steps: list[RecipeStep] = Field(..., min_length=1, description="Creation steps")
    pitfalls: list[str] = Field(default_factory=list, description="Common mistakes to avoid")
    showcase: ShowcaseConfig | None = Field(None, description="Result showcase")
    source: dict | None = Field(None, description="Origin tracking (thread_id, model, etc.)")
