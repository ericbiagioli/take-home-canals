"""Generates db/02_seed.sql: a realistic-scale, plain SQL data dump.

This script is a one-time/maintenance dev tool, not part of the running
application -- the app and the Docker setup only ever consume the SQL file
it produces, never this script. That's deliberate: database initialization
should be a SQL artifact you can read, diff, and apply with `psql`, not
logic hidden in a Python script that runs against a live app at startup.

Re-run it any time you want a differently-sized or reshuffled dataset:

    python scripts/generate_seed_dump.py > db/02_seed.sql

Uses a fixed random seed, so the output is reproducible -- running it twice
produces byte-identical SQL.

Scale (realistic for a mid-size e-commerce catalog, not enormous):
  - 500 customers
  - 16 warehouses, at real coordinates the mock geocoder also recognizes
  - ~420 products across 10 categories
  - inventory: each warehouse stocks a random 55-85% of the catalog, so
    coverage is realistically uneven (some products are everywhere, some
    are carried by only one or two warehouses, some warehouses are missing
    a given product entirely) -- around 4,500-5,000 inventory rows.

The generated INSERT statements assume they're being loaded into an empty
database: customers/warehouses/products use the database's own identity
columns for their ids, and the inventory rows reference those ids by
*insertion order* (customer 1, warehouse 1, product 1, ... in the order
generated below), the same way you'd get ids back from RETURNING on a
fresh table. Don't run this dump against a database that already has rows
in these tables -- the unique constraints (email, sku) and the id
assumption will conflict.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.geocoding import KNOWN_CITIES  # noqa: E402

RNG_SEED = 42
NUM_CUSTOMERS = 500
NUM_PRODUCTS_TARGET = 420

WAREHOUSE_CITIES = [
    "newark", "columbus", "reno", "dallas", "los angeles", "chicago",
    "houston", "phoenix", "philadelphia", "seattle", "denver", "atlanta",
    "miami", "las vegas", "minneapolis", "portland",
]

FIRST_NAMES = [
    "Ada", "Grace", "Alan", "Katherine", "Margaret", "John", "Barbara",
    "Donald", "Frances", "Edsger", "Radia", "Linus", "Tim", "Vint",
    "Marissa", "Sundar", "Satya", "Susan", "Lisa", "James", "Mary",
    "Robert", "Patricia", "Michael", "Jennifer", "William", "Elizabeth",
    "David", "Sarah", "Richard", "Karen", "Joseph", "Nancy", "Thomas",
    "Lily", "Carlos", "Maria", "Wei", "Yuki", "Fatima", "Ahmed", "Olga",
    "Sven", "Priya", "Diego", "Noor", "Hiroshi", "Elena", "Marcus", "Aisha",
]
LAST_NAMES = [
    "Lovelace", "Hopper", "Turing", "Johnson", "Hamilton", "Backus",
    "Liskov", "Knuth", "Allen", "Dijkstra", "Perlman", "Torvalds",
    "Berners-Lee", "Cerf", "Mayer", "Pichai", "Nadella", "Wojcicki",
    "Su", "Smith", "Garcia", "Martinez", "Davis", "Rodriguez", "Wilson",
    "Anderson", "Taylor", "Thomas", "Moore", "Jackson", "White", "Harris",
    "Clark", "Lewis", "Young", "King", "Wright", "Lopez", "Hill", "Scott",
    "Green", "Baker", "Adams", "Nelson", "Carter", "Mitchell", "Perez",
    "Roberts", "Turner", "Phillips",
]

# (category code, [adjectives], [nouns], price range in cents)
CATEGORIES = [
    ("ELEC", "Electronics",
     ["Wireless", "Bluetooth", "USB-C", "4K", "Smart", "Portable",
      "Noise-Cancelling", "Rechargeable", "Compact", "HD"],
     ["Earbuds", "Headphones", "Webcam", "Monitor", "Speaker", "Keyboard",
      "Mouse", "USB Hub", "Charging Cable", "Power Bank", "Smartwatch",
      "Router", "Phone Stand", "Laptop Sleeve", "Docking Station"],
     (999, 49999)),
    ("HOME", "Home & Kitchen",
     ["Stainless Steel", "Non-Stick", "Ceramic", "Glass", "Bamboo",
      "Silicone", "Insulated", "Stackable", "Adjustable", "Electric"],
     ["Water Bottle", "Cutting Board", "Mixing Bowl Set", "Knife Set",
      "Coffee Maker", "Blender", "Toaster", "Storage Containers",
      "Dish Rack", "Trash Can", "Throw Blanket", "Area Rug", "Wall Clock",
      "Picture Frame"],
     (599, 19999)),
    ("OFFC", "Office Supplies",
     ["Ergonomic", "Adjustable", "Wireless", "LED", "Foldable",
      "Heavy-Duty", "Compact"],
     ["Desk Lamp", "Office Chair", "Standing Desk", "Monitor Arm",
      "Stapler", "Notebook", "Pen Set", "File Organizer", "Whiteboard",
      "Desk Organizer", "Paper Shredder"],
     (499, 39999)),
    ("OUTD", "Outdoor & Sporting Goods",
     ["Waterproof", "Lightweight", "Insulated", "Portable", "Heavy-Duty",
      "Foldable", "All-Terrain"],
     ["Camping Tent", "Sleeping Bag", "Hiking Backpack", "Water Bottle",
      "Cooler", "Camp Chair", "Headlamp", "Trekking Poles", "Yoga Mat",
      "Resistance Bands", "Dumbbell Set", "Jump Rope", "Bike Helmet",
      "Bike Lock"],
     (999, 29999)),
    ("TOOL", "Tools & Hardware",
     ["Cordless", "Heavy-Duty", "Adjustable", "Precision",
      "Rust-Resistant"],
     ["Drill", "Hammer", "Screwdriver Set", "Wrench Set", "Tool Box",
      "Tape Measure", "Level", "Utility Knife", "Work Gloves",
      "Extension Cord", "Flashlight", "Ladder"],
     (799, 24999)),
    ("TOYS", "Toys & Games",
     ["Educational", "Interactive", "Wooden", "Glow-in-the-Dark",
      "Classic"],
     ["Building Blocks Set", "Puzzle", "Board Game", "Action Figure",
      "Plush Toy", "Remote Control Car", "Card Game", "Art Set",
      "Science Kit"],
     (599, 7999)),
    ("PETS", "Pet Supplies",
     ["Orthopedic", "Durable", "Washable", "Adjustable", "Non-Slip"],
     ["Dog Bed", "Cat Tree", "Pet Carrier", "Dog Leash",
      "Cat Litter Box", "Pet Food Bowl", "Dog Toy", "Grooming Kit",
      "Aquarium Filter"],
     (699, 14999)),
    ("BEAU", "Beauty & Personal Care",
     ["Hydrating", "Organic", "Fragrance-Free", "Anti-Aging", "Gentle"],
     ["Face Moisturizer", "Shampoo", "Hair Dryer", "Electric Toothbrush",
      "Makeup Brush Set", "Facial Cleanser", "Body Lotion",
      "Hair Straightener"],
     (499, 12999)),
    ("GROC", "Grocery & Gourmet",
     ["Organic", "Gluten-Free", "Single-Origin", "Artisan",
      "Cold-Pressed"],
     ["Coffee Beans", "Olive Oil", "Honey", "Pasta", "Granola",
      "Tea Sampler", "Hot Sauce", "Trail Mix", "Protein Powder"],
     (399, 5999)),
    ("APRL", "Apparel",
     ["Cotton", "Fleece", "Waterproof", "Lightweight",
      "Moisture-Wicking"],
     ["T-Shirt", "Hoodie", "Rain Jacket", "Running Shoes", "Sock 3-Pack",
      "Beanie", "Gloves", "Leggings", "Backpack"],
     (999, 12999)),
]


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def generate_customers(rng: random.Random, n: int) -> list[tuple]:
    rows = []
    for i in range(1, n + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        email = f"{first.lower()}.{last.lower()}{i}@example.com"
        rows.append((f"{first} {last}", email))
    return rows


def generate_warehouses() -> list[tuple]:
    rows = []
    for city in WAREHOUSE_CITIES:
        lat, lon = KNOWN_CITIES[city]
        name = f"{city.title()} Distribution Center"
        address = f"{city.title()} Distribution Center, {city.title()}, USA"
        rows.append((name, address, lat, lon))
    return rows


def generate_products(rng: random.Random, target: int) -> list[tuple]:
    rows = []
    per_category = max(1, target // len(CATEGORIES))
    for code, _label, adjectives, nouns, (lo, hi) in CATEGORIES:
        combos = [(a, n) for a in adjectives for n in nouns]
        rng.shuffle(combos)
        chosen = combos[:per_category]
        for idx, (adj, noun) in enumerate(chosen, start=1):
            name = f"{adj} {noun}"
            sku = f"{code}-{idx:04d}"
            price_cents = rng.randrange(lo, hi, 50)
            rows.append((sku, name, price_cents))
    return rows


def generate_inventory(rng: random.Random, num_warehouses: int, num_products: int) -> list[tuple]:
    rows = []
    for w_idx in range(1, num_warehouses + 1):
        coverage = rng.uniform(0.55, 0.85)
        product_ids = list(range(1, num_products + 1))
        rng.shuffle(product_ids)
        stocked = product_ids[: int(num_products * coverage)]
        for p_idx in stocked:
            # Mostly modest stock levels, occasional deep stock on a few
            # items, occasional near-zero -- not a uniform distribution,
            # which would look obviously synthetic.
            roll = rng.random()
            if roll < 0.08:
                qty = rng.randint(0, 4)
            elif roll < 0.85:
                qty = rng.randint(5, 200)
            else:
                qty = rng.randint(200, 900)
            rows.append((w_idx, p_idx, qty))
    return rows


def emit_insert(table: str, columns: list[str], rows: list[tuple], batch_size: int = 500) -> str:
    out = []
    col_list = ", ".join(columns)
    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start: batch_start + batch_size]
        values_lines = []
        for row in batch:
            formatted = []
            for value in row:
                if isinstance(value, str):
                    formatted.append(sql_str(value))
                else:
                    formatted.append(repr(value))
            values_lines.append(f"({', '.join(formatted)})")
        out.append(f"INSERT INTO {table} ({col_list}) VALUES\n" + ",\n".join(values_lines) + ";")
    return "\n\n".join(out)


def main():
    rng = random.Random(RNG_SEED)

    customers = generate_customers(rng, NUM_CUSTOMERS)
    warehouses = generate_warehouses()
    products = generate_products(rng, NUM_PRODUCTS_TARGET)
    inventory = generate_inventory(rng, len(warehouses), len(products))

    parts = [
        "-- Realistic-scale demo data for the Canals order management API.",
        "--",
        f"-- Generated by scripts/generate_seed_dump.py (random seed {RNG_SEED}) --",
        "-- do not hand-edit; regenerate instead so this file stays reproducible.",
        "-- Assumes it's loaded into an EMPTY database (see db/01_schema.sql).",
        "--",
        f"-- {len(customers)} customers, {len(warehouses)} warehouses, "
        f"{len(products)} products, {len(inventory)} inventory rows.",
        "",
        "BEGIN;",
        "",
        emit_insert("customers", ["name", "email"], customers),
        "",
        emit_insert("warehouses", ["name", "address", "latitude", "longitude"], warehouses),
        "",
        emit_insert("products", ["sku", "name", "price_cents"], products),
        "",
        emit_insert("inventory", ["warehouse_id", "product_id", "quantity"], inventory),
        "",
        "COMMIT;",
        "",
    ]
    sys.stdout.write("\n".join(parts))

    print(
        f"-- generated {len(customers)} customers, {len(warehouses)} warehouses, "
        f"{len(products)} products, {len(inventory)} inventory rows",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
