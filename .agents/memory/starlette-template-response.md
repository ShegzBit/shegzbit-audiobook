---
name: Starlette 1.x TemplateResponse signature
description: In starlette 1.x, TemplateResponse moved request out of the context dict and into the first positional argument.
---

In starlette ≥ 1.0 (confirmed on 1.3.1), the `Jinja2Templates.TemplateResponse` signature changed:

**Old (starlette < 0.29):**
```python
templates.TemplateResponse("template.html", {"request": request, "key": "val"})
```

**New (starlette ≥ 1.0):**
```python
templates.TemplateResponse(request, "template.html", {"key": "val"})
```

The old form now causes `AttributeError: 'dict' object has no attribute 'split'` because the context dict is mistakenly used as the template name.

**Why:** Starlette redesigned the API so `request` is explicit rather than buried in a dict. The `request` object is automatically injected into the template context, so it doesn't need to appear in the context dict.

**How to apply:** Any time you create a FastAPI/Starlette project and use `Jinja2Templates`, use the new signature. The `request` object is still available in templates as `request`.
