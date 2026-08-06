"""The shipped Plaid-category -> ledger-category table (classify tier 2).

Plaid tags every synced transaction with a `personal_finance_category`
(detailed value + confidence); ingest banks it as `plaid-category:` metadata
on the entry. This table turns that banked signal into the vault-template's
chart of accounts. It is deliberately conservative:

  * Targets are TEMPLATE accounts only, so the shipped table is safe for a
    brand-new vault; classify additionally applies a mapping only when the
    target account is actually open in THIS vault's chart. A household with
    a richer chart points categories at it via rules.toml:

        [plaid_category_map]
        "FOOD_AND_DRINK_COFFEE" = "Expenses:Food:Coffee"

    (overrides win over this table and may add unlisted categories).
  * TRANSFER_IN_* / TRANSFER_OUT_* / LOAN_PAYMENTS_* are absent on purpose:
    Plaid can't tell an own-account transfer from a Zelle to a friend, and
    auto-booking those would corrupt the transfers-net-to-zero invariant.
    They stay in the review queue for rules or the model tier.
"""

from __future__ import annotations

DEFAULT_PLAID_MAP: dict[str, str] = {
    # ------------------------------------------------------------- income
    "INCOME_DIVIDENDS": "Income:US:Dividends",
    "INCOME_INTEREST_EARNED": "Income:US:Interest",
    "INCOME_WAGES": "Income:US:Salary",
    "INCOME_SALARY": "Income:US:Salary",  # seen in live data alongside INCOME_WAGES
    "INCOME_RETIREMENT_PENSION": "Income:US:Other",
    "INCOME_TAX_REFUND": "Income:US:Other",
    "INCOME_UNEMPLOYMENT": "Income:US:Other",
    "INCOME_OTHER_INCOME": "Income:US:Other",
    # ------------------------------------------------------- food & drink
    "FOOD_AND_DRINK_RESTAURANT": "Expenses:Food:Dining",
    "FOOD_AND_DRINK_FAST_FOOD": "Expenses:Food:Dining",
    "FOOD_AND_DRINK_COFFEE": "Expenses:Food:Dining",
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": "Expenses:Food:Dining",
    "FOOD_AND_DRINK_VENDING_MACHINES": "Expenses:Food:Dining",
    "FOOD_AND_DRINK_GROCERIES": "Expenses:Food:Groceries",
    "FOOD_AND_DRINK_OTHER_FOOD_AND_DRINK": "Expenses:Food:Dining",
    # ------------------------------------------------- general merchandise
    "GENERAL_MERCHANDISE_BOOKSTORES_AND_NEWSSTANDS": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_CONVENIENCE_STORES": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_DEPARTMENT_STORES": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_DISCOUNT_STORES": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_ELECTRONICS": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_GIFTS_AND_NOVELTIES": "Expenses:Gifts",
    "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_PET_SUPPLIES": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_SPORTING_GOODS": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_SUPERSTORES": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_TOBACCO_AND_VAPE": "Expenses:Shopping",
    "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE": "Expenses:Shopping",
    # -------------------------------------------------------- entertainment
    "ENTERTAINMENT_TV_AND_MOVIES": "Expenses:Entertainment",
    "ENTERTAINMENT_MUSIC_AND_AUDIO": "Expenses:Entertainment",
    "ENTERTAINMENT_VIDEO_GAMES": "Expenses:Entertainment",
    "ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS": "Expenses:Entertainment",
    "ENTERTAINMENT_CASINOS_AND_GAMBLING": "Expenses:Entertainment",
    "ENTERTAINMENT_OTHER_ENTERTAINMENT": "Expenses:Entertainment",
    # -------------------------------------------------------- transportation
    "TRANSPORTATION_TAXIS_AND_RIDE_SHARES": "Expenses:Transport",
    "TRANSPORTATION_PUBLIC_TRANSIT": "Expenses:Transport",
    "TRANSPORTATION_GAS": "Expenses:Transport",
    "TRANSPORTATION_PARKING": "Expenses:Transport",
    "TRANSPORTATION_TOLLS": "Expenses:Transport",
    "TRANSPORTATION_BIKES_AND_SCOOTERS": "Expenses:Transport",
    "TRANSPORTATION_OTHER_TRANSPORTATION": "Expenses:Transport",
    # --------------------------------------------------------------- travel
    "TRAVEL_FLIGHTS": "Expenses:Travel",
    "TRAVEL_LODGING": "Expenses:Travel",
    "TRAVEL_RENTAL_CARS": "Expenses:Travel",
    "TRAVEL_OTHER_TRAVEL": "Expenses:Travel",
    # ----------------------------------------------------- rent & utilities
    "RENT_AND_UTILITIES_RENT": "Expenses:Housing",
    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": "Expenses:Utilities",
    "RENT_AND_UTILITIES_INTERNET_AND_CABLE": "Expenses:Utilities",
    "RENT_AND_UTILITIES_TELEPHONE": "Expenses:Utilities",
    "RENT_AND_UTILITIES_WATER": "Expenses:Utilities",
    "RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT": "Expenses:Utilities",
    "RENT_AND_UTILITIES_OTHER_UTILITIES": "Expenses:Utilities",
    # -------------------------------------------------------------- medical
    "MEDICAL_PRIMARY_CARE": "Expenses:Health",
    "MEDICAL_DENTAL_CARE": "Expenses:Health",
    "MEDICAL_EYE_CARE": "Expenses:Health",
    "MEDICAL_PHARMACIES_AND_SUPPLEMENTS": "Expenses:Health",
    "MEDICAL_VETERINARY_SERVICES": "Expenses:Health",
    "MEDICAL_NURSING_CARE": "Expenses:Health",
    "MEDICAL_OTHER_MEDICAL": "Expenses:Health",
    # -------------------------------------------------------- personal care
    "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS": "Expenses:Health",
    "PERSONAL_CARE_HAIR_AND_BEAUTY": "Expenses:Personal",
    "PERSONAL_CARE_LAUNDRY_AND_DRY_CLEANING": "Expenses:Personal",
    "PERSONAL_CARE_OTHER_PERSONAL_CARE": "Expenses:Personal",
    # ----------------------------------------------------- general services
    "GENERAL_SERVICES_ACCOUNTING_AND_FINANCIAL_PLANNING": "Expenses:Services",
    "GENERAL_SERVICES_AUTOMOTIVE": "Expenses:Transport",
    "GENERAL_SERVICES_CHILDCARE": "Expenses:Childcare",
    "GENERAL_SERVICES_CONSULTING_AND_LEGAL": "Expenses:Services",
    "GENERAL_SERVICES_EDUCATION": "Expenses:Education",
    "GENERAL_SERVICES_INSURANCE": "Expenses:Insurance",
    "GENERAL_SERVICES_POSTAGE_AND_SHIPPING": "Expenses:Services",
    "GENERAL_SERVICES_STORAGE": "Expenses:Services",
    "GENERAL_SERVICES_OTHER_GENERAL_SERVICES": "Expenses:Services",
    # ------------------------------------------------ government & non-profit
    "GOVERNMENT_AND_NON_PROFIT_DONATIONS": "Expenses:Gifts",
    "GOVERNMENT_AND_NON_PROFIT_GOVERNMENT_DEPARTMENTS_AND_AGENCIES": "Expenses:Services",
    "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT": "Expenses:Taxes",
    "GOVERNMENT_AND_NON_PROFIT_OTHER_GOVERNMENT_AND_NON_PROFIT": "Expenses:Services",
    # ------------------------------------------------------ home improvement
    "HOME_IMPROVEMENT_FURNITURE": "Expenses:Home",
    "HOME_IMPROVEMENT_HARDWARE": "Expenses:Home",
    "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE": "Expenses:Home",
    "HOME_IMPROVEMENT_SECURITY": "Expenses:Home",
    "HOME_IMPROVEMENT_OTHER_HOME_IMPROVEMENT": "Expenses:Home",
    # ------------------------------------------------------------ bank fees
    "BANK_FEES_ATM_FEES": "Expenses:Fees",
    "BANK_FEES_FOREIGN_TRANSACTION_FEES": "Expenses:Fees",
    "BANK_FEES_INSUFFICIENT_FUNDS": "Expenses:Fees",
    "BANK_FEES_INTEREST_CHARGE": "Expenses:Fees",
    "BANK_FEES_OVERDRAFT_FEES": "Expenses:Fees",
    "BANK_FEES_OTHER_BANK_FEES": "Expenses:Fees",
}
