"""Admin → Website Reviews & FAQs: only an admin writes, the public lists show
only what is switched on, input is validated (no blanks, no oversize text, no
script/data photo links), and the owner can reorder / hide / clear a photo."""
import pytest
from bson import ObjectId

from tests.society_factories import auth, client, make_admin, make_center, staff

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def who(db, cleanup):
    center = await make_center(db, cleanup, spot=1)
    manager, captain = await staff(db, cleanup, center["id"])
    admin = await make_admin(db, cleanup)
    cleanup.append(("faqs", {"question": {"$regex": "^CMTEST"}}))
    cleanup.append(("testimonials", {"customer_name": {"$regex": "^CMTEST"}}))
    cleanup.append(("audit_logs", {"actor_id": admin}))
    return {
        "admin": auth(admin, "admin"), "manager": auth(manager, "manager", center["id"]), "captain": auth(captain, "captain", center["id"]),
        "admin_id": admin,
    }


FAQ = {"question": "CMTEST How does the pass work?", "answer": "One car, one month, a set number of washes."}
REVIEW = {"customer_name": "CMTEST Ravi S.", "comment": "Captain came on time and the car looked new.", "rating": 5}


async def test_only_admin_writes_faqs_and_testimonials(who):
    async with client() as c:
        made = await c.post("/api/v1/faqs", headers=who["admin"], json=FAQ)
        assert made.status_code == 200 and made.json()["message"] == "FAQ created successfully"
        fid = made.json()["data"]["id"]
        for role in ("manager", "captain"):
            h = who[role]
            assert (await c.post("/api/v1/faqs", headers=h, json=FAQ)).status_code == 403
            assert (await c.put(f"/api/v1/faqs/{fid}", headers=h, json={"is_active": False})).status_code == 403
            assert (await c.delete(f"/api/v1/faqs/{fid}", headers=h)).status_code == 403
            assert (await c.post("/api/v1/testimonials", headers=h, json=REVIEW)).status_code == 403
        assert (await c.post("/api/v1/faqs", json=FAQ)).status_code in (401, 403)
        assert (await c.post("/api/v1/testimonials", json=REVIEW)).status_code in (401, 403)


async def test_public_lists_hide_switched_off_rows_but_admin_sees_them(who):
    async with client() as c:
        shown = (await c.post("/api/v1/faqs", headers=who["admin"], json={**FAQ, "question": "CMTEST shown?"})).json()["data"]
        hidden = (await c.post("/api/v1/faqs", headers=who["admin"], json={**FAQ, "question": "CMTEST hidden?", "is_active": False})).json()["data"]
        live = (await c.post("/api/v1/testimonials", headers=who["admin"], json={**REVIEW, "customer_name": "CMTEST Live"})).json()["data"]
        off = (await c.post("/api/v1/testimonials", headers=who["admin"], json={**REVIEW, "customer_name": "CMTEST Off", "is_featured": False})).json()["data"]

        # Anonymous callers get the public view even when they ask for everything.
        faqs = {f["id"] for f in (await c.get("/api/v1/faqs", params={"active_only": "false"})).json()["data"]}
        reviews = {t["id"] for t in (await c.get("/api/v1/testimonials", params={"featured_only": "false"})).json()["data"]}
        assert shown["id"] in faqs and hidden["id"] not in faqs
        assert live["id"] in reviews and off["id"] not in reviews
        # A customer-less/other-role token is no different.
        cap = {f["id"] for f in (await c.get("/api/v1/faqs", params={"active_only": "false"}, headers=who["captain"])).json()["data"]}
        assert hidden["id"] not in cap

        # The admin screen asks for hidden rows too.
        admin_faqs = {f["id"] for f in (await c.get("/api/v1/faqs", params={"active_only": "false"}, headers=who["admin"])).json()["data"]}
        admin_reviews = {t["id"] for t in (await c.get("/api/v1/testimonials", params={"featured_only": "false"}, headers=who["admin"])).json()["data"]}
        assert {shown["id"], hidden["id"]} <= admin_faqs and {live["id"], off["id"]} <= admin_reviews

        # Hiding / showing is just a flag flip, and the public list follows at once.
        await c.put(f"/api/v1/faqs/{shown['id']}", headers=who["admin"], json={"is_active": False})
        assert shown["id"] not in {f["id"] for f in (await c.get("/api/v1/faqs")).json()["data"]}
        await c.put(f"/api/v1/testimonials/{off['id']}", headers=who["admin"], json={"is_featured": True})
        assert off["id"] in {t["id"] for t in (await c.get("/api/v1/testimonials")).json()["data"]}


@pytest.mark.parametrize(
    "body",
    [
        {"question": "", "answer": "A proper answer."},
        {"question": "    ", "answer": "A proper answer."},
        {"question": "Why?", "answer": "A proper answer."},  # too short
        {"question": "x" * 201, "answer": "A proper answer."},
        {"question": "A real question?", "answer": "   "},
        {"question": "A real question?", "answer": "y" * 1501},
        {"question": "A real question?", "answer": "A proper answer.", "display_order": -1},
        {"question": "A real question?", "answer": "A proper answer.", "display_order": 10_001},
    ],
)
async def test_faq_input_is_validated(who, body):
    async with client() as c:
        assert (await c.post("/api/v1/faqs", headers=who["admin"], json=body)).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"customer_name": "", "comment": "Lovely wash today."},
        {"customer_name": "R", "comment": "Lovely wash today."},
        {"customer_name": "n" * 81, "comment": "Lovely wash today."},
        {"customer_name": "Ravi", "comment": "   "},
        {"customer_name": "Ravi", "comment": "c" * 601},
        {"customer_name": "Ravi", "comment": "Lovely wash today.", "rating": 0},
        {"customer_name": "Ravi", "comment": "Lovely wash today.", "rating": 6},
        {"customer_name": "Ravi", "comment": "Lovely wash today.", "customer_image": "javascript:alert(1)"},
        {"customer_name": "Ravi", "comment": "Lovely wash today.", "customer_image": "data:image/png;base64,AAAA"},
        {"customer_name": "Ravi", "comment": "Lovely wash today.", "display_order": -5},
    ],
)
async def test_testimonial_input_is_validated(who, body):
    async with client() as c:
        assert (await c.post("/api/v1/testimonials", headers=who["admin"], json=body)).status_code == 422


async def test_updates_are_validated_too_and_an_empty_update_is_refused(who):
    async with client() as c:
        t = (await c.post("/api/v1/testimonials", headers=who["admin"], json={**REVIEW, "customer_name": "CMTEST Upd"})).json()["data"]
        f = (await c.post("/api/v1/faqs", headers=who["admin"], json={**FAQ, "question": "CMTEST upd?"})).json()["data"]
        assert (await c.put(f"/api/v1/testimonials/{t['id']}", headers=who["admin"], json={"rating": 9})).status_code == 422
        assert (await c.put(f"/api/v1/testimonials/{t['id']}", headers=who["admin"], json={"comment": "  "})).status_code == 422
        assert (await c.put(f"/api/v1/faqs/{f['id']}", headers=who["admin"], json={"answer": ""})).status_code == 422
        assert (await c.put(f"/api/v1/faqs/{f['id']}", headers=who["admin"], json={})).status_code == 400
        assert (await c.put(f"/api/v1/faqs/{ObjectId()}", headers=who["admin"], json={"is_active": False})).status_code == 404
        assert (await c.delete(f"/api/v1/testimonials/{ObjectId()}", headers=who["admin"])).status_code == 404


async def test_text_is_trimmed_and_a_photo_can_be_set_and_cleared(who):
    async with client() as c:
        t = (await c.post(
            "/api/v1/testimonials", headers=who["admin"],
            json={**REVIEW, "customer_name": "  CMTEST Photo  ", "customer_image": "/uploads/photos/a.jpg"},
        )).json()["data"]
        assert t["customer_name"] == "CMTEST Photo" and t["customer_image"] == "/uploads/photos/a.jpg"
        # Leaving the photo out of an update keeps it ...
        kept = (await c.put(f"/api/v1/testimonials/{t['id']}", headers=who["admin"], json={"rating": 4})).json()["data"]
        assert kept["customer_image"] == "/uploads/photos/a.jpg" and kept["rating"] == 4
        # ... and an explicit blank / null removes it.
        cleared = (await c.put(f"/api/v1/testimonials/{t['id']}", headers=who["admin"], json={"customer_image": ""})).json()["data"]
        assert cleared.get("customer_image") is None
        again = (await c.put(f"/api/v1/testimonials/{t['id']}", headers=who["admin"], json={"customer_image": "https://cdn.example.com/p.png"})).json()["data"]
        assert again["customer_image"] == "https://cdn.example.com/p.png"
        nulled = (await c.put(f"/api/v1/testimonials/{t['id']}", headers=who["admin"], json={"customer_image": None})).json()["data"]
        assert nulled.get("customer_image") is None


async def test_display_order_controls_the_public_order_and_edits_are_audited(db, who):
    async with client() as c:
        a = (await c.post("/api/v1/faqs", headers=who["admin"], json={**FAQ, "question": "CMTEST first?", "display_order": 20})).json()["data"]
        b = (await c.post("/api/v1/faqs", headers=who["admin"], json={**FAQ, "question": "CMTEST second?", "display_order": 10})).json()["data"]
        order = [f["id"] for f in (await c.get("/api/v1/faqs")).json()["data"] if f["id"] in (a["id"], b["id"])]
        assert order == [b["id"], a["id"]]
        await c.put(f"/api/v1/faqs/{a['id']}", headers=who["admin"], json={"display_order": 5})
        order = [f["id"] for f in (await c.get("/api/v1/faqs")).json()["data"] if f["id"] in (a["id"], b["id"])]
        assert order == [a["id"], b["id"]]
    row = await db.audit_logs.find_one({"target_id": a["id"], "action": "UPDATE_FAQ"})
    assert row["details"]["changes"]["display_order"] == {"before": 20, "after": 5}
