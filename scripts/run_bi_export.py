"""Script to generate BI export outputs: config JSON, schema doc, and ZIP archive."""
import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from export.bi_export import get_bi_config, get_schema_doc, export_all_as_zip

OUTPUT_DIR = Path("outputs/bi_export")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 1. Get BI config and save as JSON
print("Generating Power BI configuration...")
config = get_bi_config()
config_path = OUTPUT_DIR / "powerbi_config.json"
with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, default=str)
print(f"  Saved: {config_path.resolve()}")

# 2. Get schema documentation and save as markdown
print("Generating schema documentation...")
schema_doc = get_schema_doc()
doc_path = OUTPUT_DIR / "schema_documentation.md"
with open(doc_path, "w", encoding="utf-8") as f:
    f.write(schema_doc)
print(f"  Saved: {doc_path.resolve()}")

# 3. Export all tables as ZIP
print("Exporting all tables as ZIP archive...")
zip_bytes = export_all_as_zip()
zip_path = OUTPUT_DIR / "corporate_data_all_tables.zip"
with open(zip_path, "wb") as f:
    f.write(zip_bytes)
print(f"  Saved: {zip_path.resolve()}")

# Summary
tables_count = len(config["tables"])
print(f"\nDone. Generated {tables_count} table(s) export.")
print(f"  - Power BI config: {config_path.name}")
print(f"  - Schema doc:      {doc_path.name}")
print(f"  - ZIP archive:     {zip_path.name}")
