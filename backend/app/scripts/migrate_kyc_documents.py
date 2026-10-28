"""
Move captains' KYC document images from the PUBLIC bucket into the PRIVATE
one (R2_PRIVATE_BUCKET_NAME). New uploads already go there; this handles
anything older. Idempotent, copies only (never deletes the old object —
remove it yourself after checking, see docs/DEPLOYMENT.md).

Run:  python -m app.scripts.migrate_kyc_documents            # dry run, counts only
      python -m app.scripts.migrate_kyc_documents --apply    # copy + repoint
"""
import argparse
import asyncio

from app.core.database import connect_to_mongo, get_database
from app.core.kyc_migration import migrate_r2_documents, move_local_documents, rewrite_local_document_urls


async def run(apply: bool) -> None:
    await connect_to_mongo(build_indexes=False)
    db = get_database()
    print("local files moved:", move_local_documents() if apply else "(dry run)")
    print("local urls rewritten:", await rewrite_local_document_urls(db) if apply else "(dry run)")
    print("r2:", await migrate_r2_documents(db, apply=apply))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually copy and repoint (default is a dry run)")
    asyncio.run(run(parser.parse_args().apply))
