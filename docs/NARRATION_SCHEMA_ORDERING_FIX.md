# Schema retention fix — 2026-10-30

The first live attempt paused before submission with schema error reference eefb54bf9136. Inspection of the actual AI Studio editor in the two-tab Narration/AI Studio window reproduced the missing fixture behavior without generating a response: saving a small synthetic object schema automatically adds `propertyOrdering` at the root and nested object schemas. The previous comparison sorted object keys but still rejected these extra fields.

The content script now accepts this specific normalization only when the requested schema omitted propertyOrdering and the added list contains each saved property exactly once. Explicit requested ordering and every other schema field/constraint remain compared strictly. Missing/changed properties, types, required fields, extra constraints and malformed ordering still fail before submission.

The real unpacked-extension synthetic fixture now performs the observed root/nested normalization on Save, rather than retaining the exact submitted JSON. A focused JavaScript test checks accepted normalization and rejected schema changes. The live schema-only inspection did not press Run, attach chapter images, resume a queue or contact Gemini generation. No app-server changes are required for this content-script fix. Extension version: 0.5.1.
