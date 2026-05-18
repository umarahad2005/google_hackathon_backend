"""
Zimma AI — Seed Script.

Seeds 100 synthetic providers across Islamabad/Rawalpindi sectors
with geo points, ratings, price bands, working hours, availability,
and languages. All marked is_synthetic=true.

Owner: Backend Engineer (03)
Source: agents/skills/supabase-data-layer.md §seed
"""

import json
import random
import uuid
from datetime import datetime, timedelta, timezone

from app.services.supabase import get_supabase

PKT = timezone(timedelta(hours=5))

# Sectors with (lat, lng)
SECTORS = {
    "G-10": (33.6844, 72.9975),
    "G-11": (33.6700, 72.9850),
    "G-12": (33.6550, 72.9750),
    "G-13": (33.6350, 72.9640),
    "G-14": (33.6200, 72.9500),
    "G-15": (33.6050, 72.9400),
    "F-5": (33.7220, 73.0450),
    "F-6": (33.7180, 73.0300),
    "F-7": (33.7130, 73.0150),
    "F-8": (33.7050, 73.0050),
    "F-9": (33.6950, 72.9950),
    "F-10": (33.6900, 72.9850),
    "F-11": (33.6800, 72.9750),
    "I-8": (33.6650, 73.0700),
    "I-9": (33.6550, 73.0600),
    "I-10": (33.6450, 73.0500),
    "I-11": (33.6350, 73.0400),
    "E-7": (33.7350, 73.0600),
    "E-11": (33.6800, 73.0400),
    "H-8": (33.6900, 73.0550),
    "H-9": (33.6750, 73.0450),
    "Blue Area": (33.7100, 73.0580),
    "Saddar": (33.5990, 73.0500),
    "Bahria Town": (33.5200, 73.0900),
    "DHA": (33.5150, 73.1100),
    "PWD": (33.5600, 73.0800),
}

CATEGORIES = [
    "ac_technician",
    "electrician",
    "plumber",
    "tutor",
    "beautician",
    "carpenter",
    "appliance_repair",
]

# Name pools per category (Urdu-style names)
NAME_POOLS = {
    "ac_technician": [
        "Ali AC Services", "Bilal Cooling", "Usman AC Repair",
        "Ahmed Refrigeration", "Tariq AC Solutions", "Shahid Cold Air",
        "Kamran AC Works", "Faisal Cool Tech", "Imran AC Expert",
        "Nadeem AC Maintenance", "Waqar Cooling Systems", "Hassan AC Pro",
        "Saeed AC & Fridge", "Arif Cool Services", "Zahid AC Care",
    ],
    "electrician": [
        "Rizwan Electric", "Kashif Bijli Wala", "Zubair Electric Works",
        "Nabeel Power Solutions", "Iqbal Electrician", "Sarfraz Electric",
        "Amir Wiring Expert", "Tanveer Electric", "Babar Electric Services",
        "Sajid Electric Works", "Junaid Power Fix", "Hamza Electric Care",
        "Asad Electric Pro", "Danish Electric Hub", "Yasir Electric",
    ],
    "plumber": [
        "Akbar Plumbing", "Naeem Nalka Wala", "Aslam Pipe Works",
        "Shafiq Plumber", "Mazhar Water Solutions", "Rashid Plumbing",
        "Bilal Pipe Expert", "Tahir Plumber Services", "Khalid Water Works",
        "Feroze Plumbing Co", "Naveed Pipe Fix", "Irfan Plumber Pro",
        "Saleem Water Expert", "Ghulam Plumbing", "Anwar Pipe Solutions",
    ],
    "tutor": [
        "Sir Ahmed Tuition", "Ma'am Fatima Academy", "Usman Home Tutors",
        "Hammad Tuition Center", "Sadia Home Teaching", "Fahad Academy",
        "Noor Tuition Hub", "Amna Learning Center", "Rehan Private Tutor",
        "Aliya Education", "Zain Study Circle", "Hina Home Tutors",
        "Kareem Academic Help", "Bushra Tuition", "Owais Education Pro",
    ],
    "beautician": [
        "Ayesha Beauty Parlor", "Sara Makeup Studio", "Hira Beauty",
        "Nadia Salon", "Farah Beauty Services", "Zara Glam Studio",
        "Sana Beauty Care", "Mehwish Parlor", "Rida Beauty Hub",
        "Asma Salon & Spa", "Kiran Beauty Expert", "Maham Makeup",
        "Amna Beauty Lounge", "Sidra Salon", "Lubna Beauty Art",
    ],
    "carpenter": [
        "Ismail Carpentry", "Shaukat Lakkar Wala", "Arshad Wood Works",
        "Pervaiz Furniture", "Ghaffar Carpenter", "Muneer Wood Expert",
        "Liaqat Carpentry Pro", "Bashir Furniture Fix", "Saghir Wood Craft",
        "Nazir Carpenter Services", "Mushtaq Wood Works", "Habib Carpentry",
        "Sultan Furniture Care", "Shakeel Wood Art", "Qamar Carpenter",
    ],
    "appliance_repair": [
        "Zafar Appliance Repair", "Waseem Electronics Fix", "Hamid Mender",
        "Shoaib Appliance Care", "Aamir Fix-It", "Farhan Repair Shop",
        "Tauqeer Electronics", "Sohail Appliance Pro", "Azhar Repair Works",
        "Mumtaz Fix Center", "Waheed Repair Hub", "Rafiq Electronics Repair",
        "Manzoor Appliance Fix", "Shakoor Repair Expert", "Javed Electronics Care",
    ],
}

WORKING_HOURS = {
    "standard": {"mon_fri": "09:00-18:00", "sat": "09:00-14:00", "sun": "closed"},
    "extended": {"mon_sat": "08:00-20:00", "sun": "10:00-16:00"},
    "morning": {"mon_fri": "07:00-14:00", "sat": "07:00-12:00", "sun": "closed"},
    "flexible": {"mon_sun": "08:00-22:00"},
}


def _jitter(base_lat: float, base_lng: float, radius_km: float = 2.0):
    """Add random jitter within radius to avoid exact duplicates."""
    # ~0.009 degrees ≈ 1km at this latitude
    deg_per_km = 0.009
    dlat = random.uniform(-radius_km, radius_km) * deg_per_km
    dlng = random.uniform(-radius_km, radius_km) * deg_per_km
    return round(base_lat + dlat, 6), round(base_lng + dlng, 6)


def generate_providers(count: int = 100) -> list[dict]:
    """Generate synthetic providers."""
    providers = []
    sectors = list(SECTORS.keys())

    for i in range(count):
        category = random.choice(CATEGORIES)
        sector = random.choice(sectors)
        base_lat, base_lng = SECTORS[sector]
        lat, lng = _jitter(base_lat, base_lng, radius_km=1.5)

        names = NAME_POOLS[category]
        name = names[i % len(names)]
        # Make name unique by adding sector if duplicate
        if i >= len(names):
            name = f"{name} ({sector})"

        rating = round(random.uniform(3.0, 5.0), 1)
        price_band = random.choice(["low", "mid", "high"])
        hours_key = random.choice(list(WORKING_HOURS.keys()))
        languages = random.choice([
            ["ur", "en"],
            ["ur"],
            ["ur", "en", "pn"],
            ["en", "ur"],
        ])

        providers.append({
            "name": name,
            "category": category,
            "geo": f"POINT({lng} {lat})",
            "rating": rating,
            "price_band": price_band,
            "languages": languages,
            "working_hours": WORKING_HOURS[hours_key],
            "phone": f"+92 3{random.randint(0,4)}{random.randint(0,9)} {random.randint(1000000,9999999)}",
            "is_synthetic": True,
        })

    return providers


def generate_availability(provider_id: str) -> list[dict]:
    """Generate availability slots for next 7 days."""
    slots = []
    now = datetime.now(PKT)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for day_offset in range(7):
        day = base + timedelta(days=day_offset)
        # Generate morning, midday, afternoon, evening slots
        slot_hours = [(9, 10), (10, 11), (11, 12), (14, 15), (15, 16), (16, 17), (17, 18)]
        for start_h, end_h in slot_hours:
            # Randomly mark ~20% as already booked
            is_booked = random.random() < 0.2

            slots.append({
                "provider_id": provider_id,
                "slot_start": day.replace(hour=start_h).isoformat(),
                "slot_end": day.replace(hour=end_h).isoformat(),
                "is_booked": is_booked,
            })

    return slots


def run_seed():
    """Seed the database with synthetic providers + availability."""
    sb = get_supabase()

    print(">> Seeding Zimma AI database...")
    print("=" * 50)

    # Generate providers
    providers = generate_providers(100)
    print(f"[+] Generated {len(providers)} synthetic providers")

    # Insert providers
    inserted = []
    for p in providers:
        try:
            result = sb.table("providers").insert(p).execute()
            if result.data:
                inserted.append(result.data[0])
        except Exception as e:
            print(f"  [!] Failed to insert {p['name']}: {e}")

    print(f"[OK] Inserted {len(inserted)} providers")

    # Generate and insert availability for each
    total_slots = 0
    for provider in inserted:
        slots = generate_availability(provider["id"])
        try:
            sb.table("provider_availability").insert(slots).execute()
            total_slots += len(slots)
        except Exception as e:
            print(f"  [!] Failed to insert availability for {provider['name']}: {e}")

    print(f"[OK] Inserted {total_slots} availability slots")

    # Create demo user
    try:
        sb.table("users").upsert({
            "id": "00000000-0000-0000-0000-000000000001",
            "display_name": "Demo User",
            "lang_pref": "en",
        }).execute()
        print("[OK] Demo user created")
    except Exception as e:
        print(f"  [!] Demo user creation failed: {e}")

    # Print summary by category
    print("\n[Stats] Providers by category:")
    for cat in CATEGORIES:
        count = sum(1 for p in inserted if p.get("category") == cat)
        print(f"  {cat}: {count}")

    # Print by sector
    print("\n[Stats] Providers by sector (approximate):")
    for sector, (slat, slng) in list(SECTORS.items())[:10]:
        nearby = sum(
            1 for p in providers
            if abs(float(p["geo"].split("(")[1].split()[1].rstrip(")")) - slat) < 0.02
        )
        print(f"  {sector}: ~{nearby}")

    print(f"\n[DONE] Seed complete! {len(inserted)} providers, {total_slots} slots")


if __name__ == "__main__":
    run_seed()
