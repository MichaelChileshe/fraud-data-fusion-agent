"""Generate Kgotla Financial Services' synthetic data, with a fraud ring planted in it.

Everything here is fake: names come from Faker, e-mail addresses use the
reserved example.* domains, IP addresses use the documentation ranges, and
the sanctions list is labelled SYNTH. Nothing describes a real person.

Because I plant the ring myself, I know the right answer to every question
an analyst could ask. That answer is written to data/ground_truth.json and
is what the tests and the evaluation score against.

Run:  python -m fusion.generate            (defaults: 5,000 customers, 50,000 transfers)
"""

import argparse
import json
import random
import string
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from faker import Faker

CITIES = ["Johannesburg", "Soweto", "Pretoria", "Durban", "Cape Town",
          "Gqeberha", "Bloemfontein", "Polokwane", "Mbombela", "Kimberley"]
CHANNELS = ["app", "app", "app", "web", "ussd", "card"]
REFERENCES = ["rent", "groceries", "school fees", "airtime", "loan repayment",
              "stokvel", "taxi fare", "electricity", "birthday", "salary advance",
              "church", "funeral policy", "", "", ""]
ROUTINE_NOTES = [
    "Customer called to replace a lost card. Identity verified with security questions.",
    "Address change requested through the app; proof of residence received and filed.",
    "Customer disputed a debit order from a gym. Referred to the disputes team.",
    "Card declined at a fuel station due to a daily limit. Limit explained to the customer.",
    "Customer asked how to set up a stokvel payment group. Sent the help article.",
    "Failed login alerts after the customer forgot a PIN. PIN reset completed in branch.",
    "Customer queried bank charges for the month. Fee schedule explained.",
    "Salary deposit arrived late; employer confirmed a payroll delay.",
]
# Notes on the ring. None of them uses the words "mule" or "ring", so finding
# them needs meaning-based search, not keyword matching.
RING_NOTES = [
    "Customer said a man on WhatsApp offered R1,000 a week to let a 'business partner' "
    "receive payments into this account.",
    "Many small deposits from unrelated people, each moved out within hours. "
    "Customer could not explain who the senders were.",
    "Customer admitted a friend asked to use the account for a job and that he handed over the banking app PIN.",
    "Branch reported the customer arrived with another person who answered every question on the customer's behalf.",
    "Customer claimed the incoming transfers were stokvel contributions but could not name any stokvel member.",
]


@dataclass
class Person:
    first: str
    last: str
    middle: str
    dob: date
    phone: str          # E.164, the canonical value
    email: str
    city: str


@dataclass
class World:
    kyc: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    logins: list[dict] = field(default_factory=list)
    sanctions: list[dict] = field(default_factory=list)
    case_notes: list[dict] = field(default_factory=list)
    truth: dict = field(default_factory=dict)


class Generator:
    def __init__(self, customers: int, transactions: int, seed: int, bad_rate: float,
                 dup_rate: float, as_of: date):
        self.n_customers = customers
        self.n_transactions = transactions
        self.bad_rate = bad_rate
        self.dup_rate = dup_rate
        self.rng = random.Random(seed)
        self.fake = Faker(["zu_ZA", "en_GB"])
        self.fake.seed_instance(seed)
        self.end = datetime.combine(as_of, time(0), tzinfo=timezone.utc)
        self.start = self.end - timedelta(days=90)
        self.w = World()
        self.used_phones: set[str] = set()
        self.accounts: list[str] = []
        self.counter = {"kyc": 0, "txn": 0, "login": 0, "note": 0}

    # ---------- small helpers ----------
    def _id(self, kind: str) -> str:
        self.counter[kind] += 1
        n = self.counter[kind]
        return {"kyc": f"KYC-{n:06d}", "txn": f"T{n:07d}",
                "login": f"L{n:07d}", "note": f"N-{n:05d}"}[kind]

    def _new_account(self) -> str:
        acct = f"A{len(self.accounts) + 1:06d}"
        self.accounts.append(acct)
        return acct

    def _phone(self) -> str:
        while True:
            p = "+27" + self.rng.choice("678") + "".join(self.rng.choices(string.digits, k=8))
            if p not in self.used_phones:
                self.used_phones.add(p)
                return p

    def _messy_phone(self, e164: str) -> str:
        """Write the same number the way different onboarding channels do."""
        n = e164[3:]  # 9 national digits without the leading 0
        return self.rng.choice([
            e164, "0" + n, f"0{n[:2]} {n[2:5]} {n[5:]}", f"+27 {n[:2]} {n[2:5]} {n[5:]}", "27" + n,
        ])

    def _messy_name(self, full: str) -> str:
        return self.rng.choice([full, full.upper(), f"  {full} ", full.title()])

    def _ts(self) -> datetime:
        return self.start + timedelta(seconds=self.rng.randint(0, 90 * 86400 - 1))

    def _device(self) -> str:
        return "D-" + "".join(self.rng.choices("0123456789abcdef", k=8))

    def _ip(self) -> str:
        net = self.rng.choice(["198.51.100", "203.0.113", "192.0.2"])  # documentation ranges
        return f"{net}.{self.rng.randint(1, 254)}"

    def _person(self, dob: date | None = None) -> Person:
        loc = self.fake["zu_ZA"] if self.rng.random() < 0.8 else self.fake["en_GB"]
        first, last, middle = loc.first_name(), loc.last_name(), loc.first_name()
        dob = dob or date(self.rng.randint(1951, 2006), self.rng.randint(1, 12), self.rng.randint(1, 28))
        domain = self.rng.choice(["example.com", "example.org", "example.net"])  # reserved, never real
        email = f"{first}.{last}{self.rng.randint(1, 999)}@{domain}".lower()
        return Person(first, last, middle, dob, self._phone(), email, self.rng.choice(CITIES))

    def _open(self, p: Person, name: str | None = None, phone: str | None = None,
              email: str | None | bool = True, opened: datetime | None = None) -> str:
        acct = self._new_account()
        opened = opened or (self.start - timedelta(days=self.rng.randint(30, 2000)))
        # Nobody opens an account before 18: the gate would (rightly) reject it.
        adult = datetime(p.dob.year + 18, p.dob.month, p.dob.day, tzinfo=timezone.utc)
        opened = max(opened, adult)
        self.w.kyc.append({
            "record_id": self._id("kyc"), "account_id": acct,
            "full_name": self._messy_name(name or f"{p.first} {p.last}"),
            "date_of_birth": p.dob.isoformat(),
            "phone": self._messy_phone(phone or p.phone),
            "email": (p.email if email is True else (email or None)),
            "city": p.city, "opened_at": opened.isoformat(),
        })
        return acct

    def _login(self, acct: str, device: str, ts: datetime | None = None, success: bool = True):
        self.w.logins.append({"event_id": self._id("login"), "ts": (ts or self._ts()).isoformat(),
                              "account_id": acct, "device_id": device, "ip": self._ip(),
                              "success": success})

    def _txn(self, src: str, dst: str, amount: float, ts: datetime, ref: str | None = None):
        self.w.transactions.append({
            "txn_id": self._id("txn"), "ts": ts.isoformat(), "from_account": src,
            "to_account": dst, "amount_zar": round(amount, 2),
            "channel": self.rng.choice(CHANNELS),
            "reference": self.rng.choice(REFERENCES) if ref is None else ref,
        })

    # ---------- the world ----------
    def build(self) -> World:
        rng = self.rng
        truth = self.w.truth

        # 1. Ordinary customers. About 8% open a second account later, sometimes
        #    with a shortened name and without an e-mail: the resolver must
        #    still see one person.
        people: list[tuple[Person, list[str]]] = []
        self.person_accounts: list[list[str]] = []  # one entry per real-world person
        dup_pairs = 0
        for _ in range(self.n_customers):
            p = self._person()
            accts = [self._open(p)]
            if rng.random() < 0.08:
                short = f"{p.first[0]}. {p.last}" if rng.random() < 0.5 else f"{p.first} {p.middle} {p.last}"
                accts.append(self._open(p, name=short, email=None))
                dup_pairs += 1
            people.append((p, accts))
            self.person_accounts.append(accts)
        normal_accounts = [a for _, accts in people for a in accts]

        # 2. The ring. A controller opens four accounts under variations of one
        #    name, on two phones, and recruits eight people ("mules") whose
        #    accounts are run from the controller's two devices. Three mules
        #    were registered with the controller's second phone.
        controller = Person("Sipho", "Dlamini", "M.", date(1988, 3, 14), self._phone(),
                            "s.dlamini.biz@example.net", "Johannesburg")
        phone_b = self._phone()
        opened = self.start - timedelta(days=60)
        controller_accts = [
            self._open(controller, name="Sipho M. Dlamini", opened=opened),
            self._open(controller, name="Sipho Dlamini", email=None, opened=opened + timedelta(days=2)),
            self._open(controller, name="SIPHO MANDLA DLAMINI", phone=phone_b, email=None,
                       opened=opened + timedelta(days=5)),
            self._open(controller, name="S.M. Dlamini", phone=phone_b, email=None, opened=opened + timedelta(days=9)),
        ]
        self.person_accounts.append(controller_accts)
        mule_accts = []
        for i in range(8):
            m = self._person()
            mule_accts.append(self._open(m, phone=phone_b if i < 3 else None,
                                         opened=opened + timedelta(days=10 + i)))
            self.person_accounts.append(mule_accts[-1:])
        ring_devices = [self._device(), self._device()]
        ring_device_map: dict[str, list[str]] = {}
        for acct in controller_accts + mule_accts:
            devs = ring_devices if rng.random() < 0.6 else [rng.choice(ring_devices)]
            ring_device_map[acct] = list(devs)
            for dev in devs:
                for _ in range(rng.randint(2, 5)):
                    self._login(acct, dev)

        # Money flow: victims pay mules; mules pass 85-95% to a controller
        # account within hours; controller accounts sweep to one cash-out account.
        cash_out = controller_accts[0]
        victims = rng.sample(normal_accounts, 160)
        victim_to_mule: dict[str, str] = {}
        for v in victims:
            mule = rng.choice(mule_accts)
            victim_to_mule[v] = mule
            ts = self._ts()
            amount = rng.uniform(500, 4900)
            self._txn(v, mule, amount, ts, ref=rng.choice(["deposit", "order 2291", "invest", "rent", ""]))
            fwd_to = rng.choice(controller_accts[1:])
            self._txn(mule, fwd_to, amount * rng.uniform(0.85, 0.95), ts + timedelta(hours=rng.uniform(1, 6)), ref="")
        for acct in controller_accts[1:]:
            for _ in range(12):
                self._txn(acct, cash_out, rng.uniform(4000, 15000), self._ts(), ref="")

        # 3. Decoys that look suspicious on one signal only.
        #    A household: two people share a tablet and send each other money.
        #    A spaza shop: many customers pay it, but it does not pass money on.
        hh1, hh2 = self._person(), self._person()
        hh1.city = hh2.city
        hh_a, hh_b = self._open(hh1), self._open(hh2)
        self.person_accounts += [[hh_a], [hh_b]]
        tablet = self._device()
        for acct in (hh_a, hh_b):
            for _ in range(6):
                self._login(acct, tablet)
        for _ in range(10):
            self._txn(hh_a, hh_b, rng.uniform(100, 2000), self._ts(), ref="groceries")
        shop_owner = self._person()
        shop = self._open(shop_owner)
        self.person_accounts.append([shop])
        for payer in rng.sample(normal_accounts, 120):
            self._txn(payer, shop, rng.uniform(20, 400), self._ts(), ref="spaza")

        # 4. Everyday activity for ordinary customers.
        own_devices = {a: [self._device() for _ in range(rng.randint(1, 2))] for a in normal_accounts}
        for acct, devs in own_devices.items():
            for _ in range(rng.randint(1, 5)):
                self._login(acct, rng.choice(devs), success=rng.random() > 0.05)
        remaining = self.n_transactions - len(self.w.transactions)
        for _ in range(max(0, remaining)):
            src, dst = rng.sample(normal_accounts, 2)
            self._txn(src, dst, min(rng.lognormvariate(6.6, 1.0), 90000), self._ts())
        self.w.transactions.sort(key=lambda t: t["ts"])
        self.w.logins.sort(key=lambda e: e["ts"])

        # 5. A synthetic sanctions list with one near-match for the controller.
        for i in range(1, 201):
            p = self._person()
            self.w.sanctions.append({"entry_id": f"S-{i:03d}", "full_name": f"{p.first} {p.middle} {p.last}",
                                     "date_of_birth": p.dob.isoformat() if rng.random() < 0.7 else None,
                                     "list_name": "SYNTH-CONSOLIDATED", "aliases": []})
        hit = f"S-{rng.randint(1, 200):03d}"
        for e in self.w.sanctions:
            if e["entry_id"] == hit:
                e.update(full_name="Sipho Mandla Dlamini", date_of_birth=controller.dob.isoformat(),
                         aliases=["Sipho Mandla", "S. M. Dlamini"])

        # 6. Case notes: routine ones everywhere, five tell-tale ones on ring accounts.
        for acct in rng.sample(normal_accounts, 300):
            self._note(acct, rng.choice(ROUTINE_NOTES))
        ring_note_accts = rng.sample(mule_accts, 4) + [controller_accts[2]]
        for acct, text in zip(ring_note_accts, RING_NOTES):
            self._note(acct, text)

        ring_accounts = controller_accts + mule_accts
        truth.update({
            "seed": None, "as_of": self.end.date().isoformat(),
            "ring_accounts": sorted(ring_accounts),
            "controller_accounts": controller_accts,
            "mule_accounts": mule_accts,
            "cash_out_account": cash_out,
            "ring_devices": ring_devices,
            "ring_device_map": ring_device_map,
            "ring_phones": [controller.phone, phone_b],
            "shared_phone_mules": mule_accts[:3],
            "sanctions_hit": {"entry_id": hit, "accounts": controller_accts},
            "ring_note_accounts": ring_note_accts,
            "victim_accounts": sorted(victims),
            "victim_to_mule": victim_to_mule,
            "decoys": {"household_accounts": [hh_a, hh_b], "household_device": tablet,
                       "spaza_shop_account": shop},
            "duplicate_person_pairs": dup_pairs,
        })
        protected = set(ring_accounts) | set(victims) | {hh_a, hh_b, shop}
        self._corrupt(protected)
        # After the damage: how many people and accounts should the resolver end up with?
        rejected = {r["account_id"] for r in self.w.kyc
                    if r["record_id"] in {b["id"] for b in truth["bad_records"]["kyc"]}}
        truth["expected_accounts"] = len(self.accounts) - len(rejected)
        truth["expected_persons"] = sum(1 for accts in self.person_accounts
                                        if any(a not in rejected for a in accts))
        # Ordinary customers with clean records, used as negative controls.
        touched = {a for t in self.w.transactions for a in (t["from_account"], t["to_account"])}
        truth["clean_accounts"] = [a for a in normal_accounts
                                   if a not in protected and a not in rejected and a in touched][:5]
        return self.w

    def _note(self, acct: str, text: str):
        self.w.case_notes.append({"note_id": self._id("note"), "account_id": acct,
                                  "author": f"analyst-{self.rng.randint(1, 9):02d}",
                                  "created_at": self._ts().isoformat(), "text": text})

    # ---------- deliberate damage ----------
    def _corrupt(self, protected: set[str]):
        """Damage bad_rate of the records in each source, never on protected
        accounts, and duplicate dup_rate of transactions (same id, sent twice)."""
        rng = self.rng
        future = (self.end + timedelta(days=30)).isoformat()
        breakers = {
            "transactions": [
                ("negative amount", lambda r: r.update(amount_zar=-abs(r["amount_zar"]))),
                ("sends to itself", lambda r: r.update(to_account=r["from_account"])),
                ("future timestamp", lambda r: r.update(ts=future)),
                ("implausible amount", lambda r: r.update(amount_zar=5_000_000.0)),
                ("malformed account id", lambda r: r.update(to_account="A12")),
                ("missing field", lambda r: r.pop("channel")),
            ],
            "kyc": [
                ("invalid phone", lambda r: r.update(phone="12345")),
                ("customer aged 7", lambda r: r.update(date_of_birth=(
                    datetime.fromisoformat(r["opened_at"]).date() - timedelta(days=7 * 365)).isoformat())),
                ("missing name", lambda r: r.pop("full_name")),
                ("unknown field", lambda r: r.update(favourite_colour="blue")),
            ],
            "logins": [
                ("malformed device id", lambda r: r.update(device_id="tablet-01")),
                ("future timestamp", lambda r: r.update(ts=future)),
            ],
            "case_notes": [("text too short", lambda r: r.update(text="ok"))],
        }
        acct_field = {"transactions": ("from_account", "to_account"), "kyc": ("account_id",),
                      "logins": ("account_id",), "case_notes": ("account_id",)}
        bad: dict[str, list[dict]] = {}
        for source, options in breakers.items():
            records = getattr(self.w, source)
            eligible = [r for r in records if not any(r[f] in protected for f in acct_field[source])]
            chosen = rng.sample(eligible, int(len(records) * self.bad_rate))
            id_field = {"transactions": "txn_id", "kyc": "record_id", "logins": "event_id",
                        "case_notes": "note_id"}[source]
            bad[source] = []
            for r in chosen:
                label, breaker = rng.choice(options)
                breaker(r)
                bad[source].append({"id": r[id_field], "damage": label})
        # Exact duplicates: the same event delivered twice by an upstream retry.
        clean_txns = [t for t in self.w.transactions if t["txn_id"] not in {b["id"] for b in bad["transactions"]}]
        dups = rng.sample(clean_txns, int(len(self.w.transactions) * self.dup_rate))
        for d in dups:  # re-sent later in the stream, as a retry would be
            pos = self.w.transactions.index(d)
            self.w.transactions.insert(rng.randint(pos + 1, len(self.w.transactions)), dict(d))
        self.w.truth["bad_records"] = bad
        self.w.truth["duplicate_txn_ids"] = sorted(d["txn_id"] for d in dups)


def write(world: World, out: Path, seed: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for source in ("kyc", "transactions", "logins", "sanctions", "case_notes"):
        rows = getattr(world, source)
        with open(out / f"{source}.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts[source] = len(rows)
    world.truth["seed"] = seed
    world.truth["published_counts"] = counts
    (out / "ground_truth.json").write_text(json.dumps(world.truth, indent=2), encoding="utf-8")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--customers", type=int, default=5000)
    ap.add_argument("--transactions", type=int, default=50000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--bad-rate", type=float, default=0.03)
    ap.add_argument("--dup-rate", type=float, default=0.005)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()
    as_of = datetime.now(timezone.utc).date()
    gen = Generator(args.customers, args.transactions, args.seed, args.bad_rate, args.dup_rate, as_of)
    world = gen.build()
    counts = write(world, Path(args.out), args.seed)
    bad = world.truth["bad_records"]
    print("Records written:")
    for source, n in counts.items():
        print(f"  {source:<13} {n:>7,}   damaged: {len(bad.get(source, [])):>5,}")
    print(f"  duplicate transactions: {len(world.truth['duplicate_txn_ids']):,}")
    print(f"Ring: {len(world.truth['ring_accounts'])} accounts, cash-out {world.truth['cash_out_account']}")
    print(f"Ground truth: {Path(args.out) / 'ground_truth.json'}")


if __name__ == "__main__":
    main()
