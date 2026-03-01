#\!/usr/bin/env python3
"""NexSidi Database Comprehensive Audit Script"""

import asyncio, sys, asyncpg

DB_HOST = "34.93.166.39"
DB_PORT = 5432
DB_NAME = "nexsidi-database"
DB_USER = "postgres"
DB_PASSWORD = 'J>3y&s~#F|*S)f""'

EXPECTED_SCHEMAS = ["auth","core","pipeline","chat","deploy","billing","audit","notify","public"]
INDEX_COLS = ["organization_id","user_id","email","created_at","status","project_id","session_id","run_id"]
UNIQUE_COLS = ["email","slug","api_key","token","session_id"]
SEP = "=" * 90
SUB = "-" * 70

def hdr(t): return f"\n{SEP}\n  {t}\n{SEP}"
def sub_hdr(t): return f"\n{SUB}\n  {t}\n{SUB}"


async def run_audit():
    print("Connecting to PostgreSQL at %s:%s/%s ..." % (DB_HOST, DB_PORT, DB_NAME))
    conn = await asyncpg.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME,
                                  user=DB_USER, password=DB_PASSWORD, timeout=15, ssl="prefer")
    print("Connected successfully.\n")

    schemas = await conn.fetch(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') ORDER BY schema_name")
    schema_names = [r["schema_name"] for r in schemas]
    print(hdr("0. DISCOVERED SCHEMAS"))
    for ss in schema_names:
        tag = " (expected)" if ss in EXPECTED_SCHEMAS else ""
        print(f"  - {ss}{tag}")

    print(hdr("1. ALL TABLES WITH ROW COUNTS"))
    tables = await conn.fetch(
        "SELECT schemaname, tablename FROM pg_tables "
        "WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY schemaname, tablename")
    total_tables = 0
    total_rows = 0
    for row in tables:
        sch, tbl = row["schemaname"], row["tablename"]
        try:
            cnt = await conn.fetchval(f'SELECT COUNT(*) FROM "{sch}"."{tbl}"')
        except Exception as e:
            cnt = f"ERROR: {e}"
        total_tables += 1
        if isinstance(cnt, int): total_rows += cnt
        print(f"  {sch}.{tbl:50s}  rows: {cnt}")
    print(f"\n  TOTAL: {total_tables} tables, {total_rows} rows")

    print(hdr("2. FOREIGN KEY CONSTRAINTS"))
    fks = await conn.fetch(
        "SELECT tc.constraint_name, tc.table_schema, tc.table_name, kcu.column_name, "
        "ccu.table_schema AS fk_schema, ccu.table_name AS fk_table, ccu.column_name AS fk_col, "
        "rc.delete_rule, rc.update_rule "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
        "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema "
        "JOIN information_schema.referential_constraints rc ON rc.constraint_name = tc.constraint_name AND rc.constraint_schema = tc.table_schema "
        "WHERE tc.constraint_type = 'FOREIGN KEY' ORDER BY tc.table_schema, tc.table_name, tc.constraint_name")
    print(f"  Found {len(fks)} foreign key constraints:\n")
    for fk in fks:
        print(f"  {fk['table_schema']}.{fk['table_name']}.{fk['column_name']}"
              f"  -->  {fk['fk_schema']}.{fk['fk_table']}.{fk['fk_col']}"
              f"  [ON DELETE {fk['delete_rule']}, ON UPDATE {fk['update_rule']}]"
              f"  ({fk['constraint_name']})")

    print(sub_hdr("2b. COLUMNS NAMED organization_id / user_id WITHOUT FK"))
    missing_fks = await conn.fetch(
        "WITH fk_cols AS ("
        "SELECT kcu.table_schema, kcu.table_name, kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
        "WHERE tc.constraint_type = 'FOREIGN KEY') "
        "SELECT c.table_schema, c.table_name, c.column_name "
        "FROM information_schema.columns c "
        "WHERE c.column_name IN ('organization_id','user_id') "
        "AND c.table_schema NOT IN ('pg_catalog','information_schema') "
        "AND NOT EXISTS (SELECT 1 FROM fk_cols fk WHERE fk.table_schema = c.table_schema AND fk.table_name = c.table_name AND fk.column_name = c.column_name) "
        "ORDER BY c.table_schema, c.table_name, c.column_name")
    if missing_fks:
        for m in missing_fks:
            print(f"  WARNING: {m['table_schema']}.{m['table_name']}.{m['column_name']} has NO FK constraint")
    else:
        print("  All organization_id / user_id columns have FK constraints.")

    print(hdr("3. INDEXES"))
    indexes = await conn.fetch(
        "SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY schemaname, tablename, indexname")
    print(f"  Found {len(indexes)} indexes:\n")
    for idx in indexes:
        print(f"  {idx['schemaname']}.{idx['tablename']}  =>  {idx['indexname']}")
        print(f"      {idx['indexdef']}")

    print(sub_hdr("3b. MISSING INDEXES ON COMMONLY-QUERIED COLUMNS"))
    mi = False
    for cn in INDEX_COLS:
        miss = await conn.fetch(
            "SELECT c.table_schema, c.table_name, c.column_name "
            "FROM information_schema.columns c "
            "WHERE c.column_name = $1 AND c.table_schema NOT IN ('pg_catalog','information_schema') "
            "AND NOT EXISTS (SELECT 1 FROM pg_indexes pi WHERE pi.schemaname = c.table_schema "
            "AND pi.tablename = c.table_name AND pi.indexdef ILIKE $2) "
            "ORDER BY c.table_schema, c.table_name", cn, f"%{cn}%")
        if miss:
            mi = True
            for m in miss:
                print(f"  MISSING INDEX: {m['table_schema']}.{m['table_name']}.{m['column_name']}")
    if not mi:
        print("  All commonly-queried columns have indexes.")

    print(hdr("4. ROW-LEVEL SECURITY (RLS) AUDIT"))
    org_tables = await conn.fetch(
        "SELECT c.table_schema, c.table_name FROM information_schema.columns c "
        "WHERE c.column_name = 'organization_id' AND c.table_schema NOT IN ('pg_catalog','information_schema') "
        "ORDER BY c.table_schema, c.table_name")
    rls_rows = await conn.fetch(
        "SELECT n.nspname AS sn, c.relname AS tn, c.relrowsecurity AS re, c.relforcerowsecurity AS rf "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog','information_schema')")
    rls_map = {(r["sn"], r["tn"]): r for r in rls_rows}
    pols = await conn.fetch(
        "SELECT n.nspname AS sn, c.relname AS tn, p.polname, p.polcmd, p.polpermissive, "
        "pg_get_expr(p.polqual, p.polrelid) AS uexpr, pg_get_expr(p.polwithcheck, p.polrelid) AS cexpr "
        "FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace ORDER BY n.nspname, c.relname, p.polname")
    pmap = {}
    for pp in pols:
        pmap.setdefault((pp["sn"], pp["tn"]), []).append(pp)
    print(f"\n  Tables with organization_id column: {len(org_tables)}\n")
    rls_miss = 0
    cmd_map = {"*":"ALL","r":"SELECT","a":"INSERT","w":"UPDATE","d":"DELETE"}
    for ot in org_tables:
        key = (ot["table_schema"], ot["table_name"])
        rls = rls_map.get(key)
        en = rls["re"] if rls else False
        fo = rls["rf"] if rls else False
        ps = pmap.get(key, [])
        st = "ENABLED" if en else "DISABLED"
        fs = ", FORCED" if fo else ""
        tg = "" if en else "  *** NO RLS ***"
        if not en: rls_miss += 1
        print(f"  {ot['table_schema']}.{ot['table_name']:45s}  RLS: {st}{fs}{tg}")
        for pol in ps:
            cm = cmd_map.get(pol["polcmd"], pol["polcmd"])
            print(f"      Policy: {pol['polname']}  cmd={cm}  permissive={pol['polpermissive']}")
            if pol["uexpr"]: print(f"        USING: {pol['uexpr']}")
            if pol["cexpr"]: print(f"        CHECK: {pol['cexpr']}")
    print(f"\n  Summary: {rls_miss} tables with organization_id but NO RLS enabled")

    print(sub_hdr("4b. ALL RLS POLICIES IN DATABASE"))
    if pols:
        for pp in pols:
            print(f"  {pp['sn']}.{pp['tn']}  policy={pp['polname']}")
    else:
        print("  No RLS policies found in the database.")

    print(hdr("5. ORPHAN RISK ANALYSIS (FK without CASCADE/SET NULL)"))
    risky = [fk for fk in fks if fk["delete_rule"] not in ("CASCADE","SET NULL","SET DEFAULT")]
    if risky:
        for fk in risky:
            print(f"  RISK: {fk['table_schema']}.{fk['table_name']}.{fk['column_name']}"
                  f"  -->  {fk['fk_schema']}.{fk['fk_table']}.{fk['fk_col']}"
                  f"  ON DELETE {fk['delete_rule']}  ({fk['constraint_name']})")
    else:
        print("  All FK constraints have CASCADE or SET NULL -- no orphan risk.")
    print(f"\n  Total risky FKs (NO ACTION / RESTRICT): {len(risky)}")

    print(hdr("6. COLUMN TYPE AUDIT"))
    print(sub_hdr("6a. TEXT columns (unbounded -- potential DoS risk)"))
    tcols = await conn.fetch(
        "SELECT table_schema, table_name, column_name FROM information_schema.columns "
        "WHERE data_type = 'text' AND table_schema NOT IN ('pg_catalog','information_schema') "
        "ORDER BY table_schema, table_name, column_name")
    if tcols:
        for tc in tcols:
            print(f"  {tc['table_schema']}.{tc['table_name']}.{tc['column_name']}  type=TEXT (unbounded)")
    else:
        print("  No unbounded TEXT columns found.")
    print(f"  Total unbounded TEXT columns: {len(tcols)}")

    print(sub_hdr("6b. TIMESTAMP columns WITHOUT timezone"))
    tscols = await conn.fetch(
        "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
        "WHERE data_type = 'timestamp without time zone' AND table_schema NOT IN ('pg_catalog','information_schema') "
        "ORDER BY table_schema, table_name, column_name")
    if tscols:
        for tc in tscols:
            print(f"  WARNING: {tc['table_schema']}.{tc['table_name']}.{tc['column_name']}  type={tc['data_type']}")
    else:
        print("  All timestamp columns use timestamp with time zone. Good.")
    print(f"  Total timestamp-without-tz columns: {len(tscols)}")

    print(hdr("7. UNIQUE CONSTRAINT AUDIT"))
    uniques = await conn.fetch(
        "SELECT tc.table_schema, tc.table_name, tc.constraint_name, kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
        "WHERE tc.constraint_type IN ('UNIQUE','PRIMARY KEY') AND tc.table_schema NOT IN ('pg_catalog','information_schema') "
        "ORDER BY tc.table_schema, tc.table_name, tc.constraint_name")
    print(f"  Found {len(uniques)} unique/PK constraints:\n")
    for u in uniques:
        print(f"  {u['table_schema']}.{u['table_name']}.{u['column_name']}  ({u['constraint_name']})")

    print(sub_hdr("7b. BUSINESS-KEY COLUMNS MISSING UNIQUE CONSTRAINTS"))
    uset = {(u["table_schema"], u["table_name"], u["column_name"]) for u in uniques}
    mu = False
    for cn in UNIQUE_COLS:
        cols = await conn.fetch(
            "SELECT table_schema, table_name, column_name FROM information_schema.columns "
            "WHERE column_name = $1 AND table_schema NOT IN ('pg_catalog','information_schema') "
            "ORDER BY table_schema, table_name", cn)
        for c in cols:
            key = (c["table_schema"], c["table_name"], c["column_name"])
            if key not in uset:
                ic = await conn.fetchval(
                    "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = $1 AND tablename = $2 "
                    "AND indexdef ILIKE '%unique%' AND indexdef ILIKE $3",
                    c["table_schema"], c["table_name"], f"%{c['column_name']}%")
                if not ic:
                    mu = True
                    print(f"  MISSING UNIQUE: {c['table_schema']}.{c['table_name']}.{c['column_name']}")
    if not mu:
        print("  All business-key columns have unique constraints.")

    print(hdr("8. AUDIT SUMMARY"))
    print(f"  Schemas discovered:          {len(schema_names)}")
    print(f"  Total tables:                {total_tables}")
    print(f"  Total rows:                  {total_rows}")
    print(f"  Foreign keys:                {len(fks)}")
    print(f"  Missing FK (org/user_id):    {len(missing_fks)}")
    print(f"  Indexes:                     {len(indexes)}")
    print(f"  RLS-eligible tables:         {len(org_tables)}")
    print(f"  RLS missing:                 {rls_miss}")
    print(f"  Orphan-risk FKs:             {len(risky)}")
    print(f"  Unbounded TEXT columns:      {len(tcols)}")
    print(f"  Timestamps without tz:       {len(tscols)}")
    print(f"  Unique/PK constraints:       {len(uniques)}")
    print(f"\n{SEP}")
    print("  Audit complete.")
    print(SEP)
    await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_audit())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)