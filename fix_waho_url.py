"""One-shot script to fix the malformed WAHO detail_url_template in the database."""
import json
import os
import sys

from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"'))

from sqlalchemy import create_engine, text

db_url = os.environ.get("TI_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not db_url:
    print("ERROR: No database URL found in environment")
    sys.exit(1)

engine = create_engine(db_url)

with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT id, name, source_type, parser_config
        FROM sources
        WHERE source_type IN ('waho', 'wahoo', 'paginated_html_list')
           OR name ILIKE '%waho%'
    """))
    rows = result.fetchall()
    if not rows:
        print("No WAHO source found in database")
        sys.exit(0)

    for row in rows:
        source_id, name, source_type, parser_config = row
        print(f"\nSource: {name} (id={source_id}, type={source_type})")
        if parser_config is None:
            print("  parser_config is NULL — skipping")
            continue

        config = parser_config if isinstance(parser_config, dict) else json.loads(parser_config)
        template = config.get("detail_url_template", "")
        print(f"  Current template: {template!r}")

        if "\u00b0" in template or "°" in template:
            fixed_template = template.replace("\u00b0", "").replace("°", "")
            print(f"  Fixed template:   {fixed_template!r}")
            config["detail_url_template"] = fixed_template
            conn.execute(
                text("UPDATE sources SET parser_config = :config WHERE id = :id"),
                {"config": json.dumps(config), "id": source_id},
            )
            print("  Updated")
        else:
            print("  No malformed characters found — skipping")

    conn.commit()
    print("\nDone.")
