"""Generate synthetic workforce data for 5 fictional clients.

Each client is inspired by a real industry archetype but fully anonymized.
"""

import csv
import random
from pathlib import Path

random.seed(42)

CLIENTS = [
    {
        "id": "client-meridian-ins",
        "name": "Meridian Insurance Group",
        "industry": "Insurance / Financial Services",
        "headcount": 120,
        "departments": ["Actuarial", "Underwriting", "Claims", "IT", "Sales", "HR", "Finance", "Legal"],
        "titles_by_dept": {
            "Actuarial": ["Actuary", "Senior Actuary", "Chief Actuary", "Actuarial Analyst"],
            "Underwriting": ["Underwriter", "Senior Underwriter", "Underwriting Manager"],
            "Claims": ["Claims Adjuster", "Claims Supervisor", "Claims Director"],
            "IT": ["Software Engineer", "Senior Engineer", "IT Manager", "Data Engineer"],
            "Sales": ["Account Executive", "Regional Sales Manager", "VP Sales"],
            "HR": ["HR Generalist", "HR Business Partner", "CHRO"],
            "Finance": ["Financial Analyst", "Controller", "CFO"],
            "Legal": ["Corporate Counsel", "Compliance Officer", "General Counsel"],
        },
    },
    {
        "id": "client-atlas-motors",
        "name": "Atlas Motors Corporation",
        "industry": "Automotive / Manufacturing",
        "headcount": 200,
        "departments": ["Manufacturing", "Engineering", "R&D", "Supply Chain", "Sales", "HR", "Finance", "EV Division"],
        "titles_by_dept": {
            "Manufacturing": ["Line Technician", "Shift Supervisor", "Plant Manager", "Quality Inspector"],
            "Engineering": ["Mechanical Engineer", "Senior Engineer", "Engineering Director"],
            "R&D": ["Research Scientist", "EV Battery Specialist", "R&D Manager"],
            "Supply Chain": ["Logistics Coordinator", "Procurement Manager", "VP Supply Chain"],
            "Sales": ["Dealer Relations Manager", "Regional Sales Director", "VP Sales"],
            "HR": ["HR Generalist", "Talent Acquisition Lead", "VP People"],
            "Finance": ["Financial Analyst", "Cost Accountant", "CFO"],
            "EV Division": ["EV Systems Engineer", "Charging Infrastructure Lead", "EV Program Director"],
        },
    },
    {
        "id": "client-helix-pharma",
        "name": "Helix Pharmaceuticals",
        "industry": "Pharmaceuticals / Life Sciences",
        "headcount": 150,
        "departments": ["Research", "Clinical Trials", "Regulatory", "Manufacturing", "Commercial", "IT", "HR", "Medical Affairs"],
        "titles_by_dept": {
            "Research": ["Research Scientist", "Principal Scientist", "VP Research"],
            "Clinical Trials": ["Clinical Research Associate", "Trial Manager", "VP Clinical"],
            "Regulatory": ["Regulatory Affairs Specialist", "Regulatory Director"],
            "Manufacturing": ["Process Engineer", "QA Manager", "Plant Director"],
            "Commercial": ["Sales Representative", "Marketing Manager", "VP Commercial"],
            "IT": ["Data Scientist", "Software Engineer", "CTO"],
            "HR": ["HR Business Partner", "Talent Development Manager", "CHRO"],
            "Medical Affairs": ["Medical Science Liaison", "Medical Director"],
        },
    },
    {
        "id": "client-velocity-logistics",
        "name": "Velocity Logistics International",
        "industry": "Logistics / Transportation",
        "headcount": 250,
        "departments": ["Operations", "Drivers", "Warehouse", "Technology", "Sales", "HR", "Finance", "Safety"],
        "titles_by_dept": {
            "Operations": ["Operations Coordinator", "Route Planner", "Operations Manager", "VP Operations"],
            "Drivers": ["Delivery Driver", "Long-Haul Driver", "Fleet Supervisor"],
            "Warehouse": ["Warehouse Associate", "Shift Lead", "Distribution Manager"],
            "Technology": ["Software Engineer", "Data Analyst", "VP Technology"],
            "Sales": ["Account Manager", "Enterprise Sales Director", "VP Sales"],
            "HR": ["HR Coordinator", "HR Manager", "VP People"],
            "Finance": ["Payroll Specialist", "Financial Analyst", "CFO"],
            "Safety": ["Safety Inspector", "Compliance Manager", "VP Safety"],
        },
    },
    {
        "id": "client-summit-hospitality",
        "name": "Summit Hospitality Group",
        "industry": "Hospitality / Hotels",
        "headcount": 180,
        "departments": ["Front Desk", "Food & Beverage", "Housekeeping", "Management", "Sales", "HR", "Finance", "IT"],
        "titles_by_dept": {
            "Front Desk": ["Front Desk Agent", "Guest Services Manager", "Front Office Director"],
            "Food & Beverage": ["Server", "Chef", "F&B Manager", "Executive Chef"],
            "Housekeeping": ["Room Attendant", "Housekeeping Supervisor", "Executive Housekeeper"],
            "Management": ["General Manager", "Assistant GM", "Regional VP"],
            "Sales": ["Event Sales Coordinator", "Group Sales Manager", "VP Revenue"],
            "HR": ["HR Coordinator", "Training Manager", "VP People"],
            "Finance": ["Night Auditor", "Financial Analyst", "Controller"],
            "IT": ["Systems Administrator", "IT Manager", "VP Technology"],
        },
    },
]

FIRST_NAMES = [
    "Alex", "Jordan", "Casey", "Taylor", "Morgan", "Riley", "Quinn", "Avery",
    "Dakota", "Jamie", "Skyler", "Rowan", "Charlie", "Emerson", "Finley",
    "Harper", "Sage", "Blair", "Reese", "Peyton", "Drew", "Cameron", "Hayden",
    "Logan", "Parker", "Kai", "River", "Phoenix", "Marley", "Lennon",
    "Remy", "Aspen", "Ellis", "Tatum", "Shiloh", "Wren", "Sutton", "Oakley",
    "August", "Briar", "Cypress", "Dalton", "Everett", "Greer", "Haven",
    "Indigo", "Jules", "Keegan", "Landry", "Marlowe",
]

LAST_NAMES = [
    "Chen", "Patel", "Kim", "Santos", "Williams", "Johnson", "Rivera",
    "Fischer", "Park", "Nguyen", "Davis", "Campbell", "Thompson", "Burke",
    "Torres", "Yamamoto", "Clarke", "Okafor", "Morrison", "Zhang",
    "Garcia", "Anderson", "Lee", "Martinez", "Taylor", "Brown", "Wilson",
    "Jackson", "White", "Harris", "Lewis", "Walker", "Hall", "Allen",
    "Young", "King", "Wright", "Scott", "Green", "Baker",
]


def generate_csv(client: dict, output_dir: Path):
    """Generate a workforce CSV for a single client."""
    rows = []
    emp_id = 1

    # Distribute headcount roughly across departments
    dept_list = client["departments"]
    base_per_dept = client["headcount"] // len(dept_list)
    remainder = client["headcount"] % len(dept_list)

    for i, dept in enumerate(dept_list):
        dept_count = base_per_dept + (1 if i < remainder else 0)
        titles = client["titles_by_dept"][dept]

        for _ in range(dept_count):
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            title = random.choice(titles)
            age = random.randint(22, 62)
            tenure = round(random.uniform(0.3, min(age - 21, 25.0)), 1)

            # Turnover risk correlated with tenure and age
            if tenure < 1.5:
                risk = random.choices(["High", "Medium", "Low"], weights=[50, 35, 15])[0]
            elif tenure < 4:
                risk = random.choices(["High", "Medium", "Low"], weights=[15, 50, 35])[0]
            else:
                risk = random.choices(["High", "Medium", "Low"], weights=[5, 25, 70])[0]

            rows.append({
                "employee_id": f"E{emp_id:04d}",
                "name": f"{first} {last}",
                "department": dept,
                "title": title,
                "age": age,
                "tenure_years": tenure,
                "turnover_risk": risk,
            })
            emp_id += 1

    # Write CSV
    output_path = output_dir / f"{client['id']}.csv"
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["employee_id", "name", "department", "title", "age", "tenure_years", "turnover_risk"])
        writer.writeheader()
        writer.writerows(rows)

    # Write client metadata
    meta_path = output_dir / f"{client['id']}.json"
    import json
    meta = {
        "client_id": client["id"],
        "client_name": client["name"],
        "industry": client["industry"],
        "headcount": client["headcount"],
        "departments": client["departments"],
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    print(f"  {client['name']}: {len(rows)} employees -> {output_path.name}")


def main():
    output_dir = Path("sample_data/clients")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Generating synthetic workforce data:")
    for client in CLIENTS:
        generate_csv(client, output_dir)

    # Write a manifest
    import json
    manifest = [
        {"client_id": c["id"], "client_name": c["name"], "industry": c["industry"]}
        for c in CLIENTS
    ]
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nManifest: {len(manifest)} clients")
    print("Done.")


if __name__ == "__main__":
    main()
