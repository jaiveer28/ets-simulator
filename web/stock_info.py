"""
stock_info.py
=============
Static, educational background on each company, for the Stock Information page.

=====================  WHY THIS FILE CONTAINS NO NUMBERS  =====================
Descriptions here are deliberately limited to WHAT THE BUSINESS DOES. They
contain no share prices, no returns, no market-cap rankings, no "best
performing", and no dated events from inside the simulation window (2021-2026).

That restraint is a no-lookahead requirement, not a style choice. Writing
"Nvidia's shares soared on the AI boom" or "became the world's most valuable
company" would tell a user in simulated 2021 what happens in 2024 -- leaking
future information through prose instead of through a price feed. Prose is the
easiest place for lookahead to sneak in, so keep these timeless.

The page itself iterates the tradable universe READ FROM THE DATABASE and looks
up profiles here by ticker, so adding a stock to the DB cannot silently drop it
from the page -- it falls back to a neutral placeholder instead.
==============================================================================
"""

PROFILES = {
    "RELIANCE.NS": {
        "sector": "Energy & Conglomerate",
        "region": "India",
        "currency": "INR",
        "description": "India's largest private-sector conglomerate, spanning "
                       "oil refining and petrochemicals, telecommunications "
                       "(Jio), and retail.",
    },
    "HDFCBANK.NS": {
        "sector": "Banking & Financial Services",
        "region": "India",
        "currency": "INR",
        "description": "One of India's largest private-sector banks, providing "
                       "retail and corporate banking, lending, and payments.",
    },
    "MARUTI.NS": {
        "sector": "Automotive",
        "region": "India",
        "currency": "INR",
        "description": "India's largest passenger-car manufacturer, majority "
                       "owned by Japan's Suzuki, known for small and mid-size "
                       "cars.",
    },
    "AAPL": {
        "sector": "Consumer Technology",
        "region": "United States",
        "currency": "USD",
        "description": "Designs and sells the iPhone, Mac, iPad and wearables, "
                       "alongside a services business including the App Store "
                       "and subscriptions.",
    },
    "TSLA": {
        "sector": "Automotive & Clean Energy",
        "region": "United States",
        "currency": "USD",
        "description": "Designs and manufactures electric vehicles, battery "
                       "storage systems, and solar energy products.",
    },
    "NVDA": {
        "sector": "Semiconductors",
        "region": "United States",
        "currency": "USD",
        "description": "Designs graphics processors (GPUs) and accelerated "
                       "computing hardware used in gaming, data centres, and "
                       "artificial intelligence.",
    },
    "AMZN": {
        "sector": "E-commerce & Cloud Computing",
        "region": "United States",
        "currency": "USD",
        "description": "Operates a global online marketplace and Amazon Web "
                       "Services, a cloud-computing platform for businesses.",
    },
    "JPM": {
        "sector": "Banking & Financial Services",
        "region": "United States",
        "currency": "USD",
        "description": "A major US bank spanning consumer and commercial "
                       "banking, investment banking, and asset management.",
    },
    "ASML": {
        "sector": "Semiconductor Equipment",
        "region": "Netherlands (US-listed ADR)",
        "currency": "USD",
        "description": "Dutch manufacturer of photolithography machines used "
                       "to produce advanced semiconductors, including extreme "
                       "ultraviolet (EUV) systems.",
    },
    "MC.PA": {
        "sector": "Luxury Goods",
        "region": "France",
        "currency": "EUR",
        "description": "A luxury goods group whose brands include Louis "
                       "Vuitton, Dior, and Moet Hennessy, spanning fashion, "
                       "leather goods, wines and spirits.",
    },
}

FALLBACK = {
    "sector": "--",
    "region": "--",
    "currency": "--",
    "description": "No background information on file for this company yet.",
}


def profile(ticker):
    """Static background for a ticker, or a neutral placeholder if unknown."""
    return PROFILES.get(ticker, FALLBACK)
