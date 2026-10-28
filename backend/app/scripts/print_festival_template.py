"""Prints the exact JSON body of Meta's create-template call for the festival
greeting template. No network, no database.

    cd backend && .venv/bin/python -m app.scripts.print_festival_template [--header-handle H] [--name N]

POST it to https://graph.facebook.com/<version>/<WABA_ID>/message_templates
(Authorization: Bearer <token>) or paste the components into Meta Business
Manager. Meta needs a sample-image handle for the IMAGE header: upload a
sample picture with the Resumable Upload API (app id -> /<APP_ID>/uploads)
and pass the returned handle as --header-handle; without it the placeholder
stays in the JSON for you to replace."""
import argparse
import json

from app.services.whatsapp_campaign_template import festival_template_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--header-handle", default=None)
    parser.add_argument("--name", default=None)
    args = parser.parse_args()
    print(json.dumps(festival_template_payload(args.header_handle, args.name), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
