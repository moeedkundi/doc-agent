"""Static, fully fictional reference data for the synthetic document generator.

All companies are invented. Products are generic office/IT supplies with no brand names.
Tax rates and FX factors are illustrative only. The vendor list is the seed for the
reference server's vendor master (Phase 3), so keep names/tax IDs stable once published.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Country = Literal["US", "GB", "DE", "NL"]


@dataclass(frozen=True)
class Company:
    code: str
    """Short code used as invoice-number prefix (vendors) or for ids."""
    name: str
    address_lines: tuple[str, ...]
    country: Country
    tax_id: str

    @property
    def address(self) -> str:
        """Single-line address as stored in ground truth."""
        return ", ".join(self.address_lines)


@dataclass(frozen=True)
class Product:
    sku: str
    description: str
    """Buyer-side wording, used on purchase orders."""
    invoice_description: str
    """Vendor-side wording, used on invoices (differs to exercise fuzzy line matching)."""
    unit_price_usd: Decimal


CURRENCY: dict[Country, str] = {"US": "USD", "GB": "GBP", "DE": "EUR", "NL": "EUR"}

# Illustrative standard rates (US: a typical combined state+local sales tax).
TAX_RATE: dict[Country, Decimal] = {
    "US": Decimal("0.0725"),
    "GB": Decimal("0.20"),
    "DE": Decimal("0.19"),
    "NL": Decimal("0.21"),
}

# Illustrative price-list factors from the USD base price.
PRICE_FACTOR: dict[str, Decimal] = {
    "USD": Decimal(1),
    "GBP": Decimal("0.79"),
    "EUR": Decimal("0.92"),
}

VENDORS: tuple[Company, ...] = (
    Company(
        "QOS",
        "Quillfeather Office Supply Co.",
        ("418 Corliss Avenue", "Dayton, OH 45402", "USA"),
        "US",
        "31-4825097",
    ),
    Company(
        "TID",
        "Tallowmere IT Distribution Inc.",
        ("2210 Bramble Court, Suite 300", "Raleigh, NC 27601", "USA"),
        "US",
        "56-2093318",
    ),
    Company(
        "IAS",
        "Inkwell & Ashby Supplies LLC",
        ("77 Wexford Street", "Hartford, CT 06103", "USA"),
        "US",
        "06-7734120",
    ),
    Company(
        "BCN",
        "Bluecairn Networks Inc.",
        ("1905 Tamarind Drive", "Boise, ID 83702", "USA"),
        "US",
        "82-1167045",
    ),
    Company(
        "SPT",
        "Saltmarsh Print & Toner LLC",
        ("640 Harbor Gate Road", "Savannah, GA 31401", "USA"),
        "US",
        "58-3390271",
    ),
    Company(
        "PDA",
        "Pinewick Data Accessories Inc.",
        ("3125 Ledgerock Parkway", "Madison, WI 53703", "USA"),
        "US",
        "39-2058816",
    ),
    Company(
        "GTS",
        "Glimmerstone Tech Supply LLC",
        ("58 Orchard Mill Lane", "Spokane, WA 99201", "USA"),
        "US",
        "91-4472390",
    ),
    Company(
        "HPC",
        "Harrowfield Paper Company Inc.",
        ("900 Kilnworth Boulevard", "Akron, OH 44308", "USA"),
        "US",
        "34-1180264",
    ),
    Company(
        "BLS",
        "Brightledger Stationers Ltd",
        ("Unit 4, Fallowfield Trading Estate", "Leeds LS11 5QR", "United Kingdom"),
        "GB",
        "GB284719306",
    ),
    Company(
        "CKC",
        "Copperkey Computing Ltd",
        ("12 Mortlake Yard", "Bristol BS1 6TQ", "United Kingdom"),
        "GB",
        "GB617204958",
    ),
    Company(
        "FHP",
        "Fernhollow Peripherals Ltd",
        ("3 Kestrel Way", "Norwich NR3 1HG", "United Kingdom"),
        "GB",
        "GB402958173",
    ),
    Company(
        "OWS",
        "Oakhaven Workplace Supplies Ltd",
        ("85 Tanners Row", "Leicester LE1 4PW", "United Kingdom"),
        "GB",
        "GB739160524",
    ),
    Company(
        "MFO",
        "Marrowgate Furniture & Office Ltd",
        ("Marrowgate House, 21 Quarry Lane", "Derby DE1 2RT", "United Kingdom"),
        "GB",
        "GB158392047",
    ),
    Company(
        "PWK",
        "Papierwerk Kessler GmbH",
        ("Lindenhofstraße 44", "34117 Kassel", "Germany"),
        "DE",
        "DE318402765",
    ),
    Company(
        "RKB",
        "Rotkehlchen Bürobedarf GmbH",
        ("Am Kornspeicher 9", "28195 Bremen", "Germany"),
        "DE",
        "DE274915038",
    ),
    Company(
        "HDT",
        "Heidekamp Digitaltechnik GmbH",
        ("Ostring 117", "44787 Bochum", "Germany"),
        "DE",
        "DE196530427",
    ),
    Company(
        "SKZ",
        "Sterndorf Kabel & Zubehör GmbH",
        ("Gewerbepark Süd 6", "93053 Regensburg", "Germany"),
        "DE",
        "DE342081659",
    ),
    Company(
        "VDK",
        "Van Drieklaver Kantoorartikelen B.V.",
        ("Lijnbaanskade 31", "3512 GK Utrecht", "Netherlands"),
        "NL",
        "NL853106724B01",
    ),
    Company(
        "LMO",
        "Lindqvist & Moor Office B.V.",
        ("Zeeltjesweg 210", "5611 HV Eindhoven", "Netherlands"),
        "NL",
        "NL412967380B01",
    ),
    Company(
        "KIT",
        "Kroonveld IT Services B.V.",
        ("Havenstraat 58", "2011 CS Haarlem", "Netherlands"),
        "NL",
        "NL690243517B01",
    ),
)

BUYERS: tuple[Company, ...] = (
    Company(
        "LLI",
        "Larkspur Logistics Inc.",
        ("5500 Wrenfield Road", "Columbus, OH 43215", "USA"),
        "US",
        "31-9902741",
    ),
    Company(
        "MDG",
        "Meadowbrook Dental Group PC",
        ("140 Sycamore Plaza", "Asheville, NC 28801", "USA"),
        "US",
        "56-4417203",
    ),
    Company(
        "RFA",
        "Redfern Analytics Inc.",
        ("88 Granary Square, Floor 6", "Minneapolis, MN 55401", "USA"),
        "US",
        "41-2209385",
    ),
    Company(
        "TWA",
        "Tidewater Architects LLP",
        ("9 Chandlers Wharf", "Southampton SO14 3QB", "United Kingdom"),
        "GB",
        "GB905318462",
    ),
    Company(
        "SBA",
        "Silverbirch Academy Trust",
        ("Birchfield Campus, Mill Road", "Reading RG1 8HX", "United Kingdom"),
        "GB",
        "GB571046239",
    ),
    Company(
        "OMB",
        "Ostwind Maschinenbau GmbH",
        ("Industriestraße 72", "70565 Stuttgart", "Germany"),
        "DE",
        "DE287604913",
    ),
    Company(
        "GRA",
        "Graafschap Accountants B.V.",
        ("Stationsplein 14", "7511 JD Enschede", "Netherlands"),
        "NL",
        "NL204581936B01",
    ),
)

PRODUCTS: tuple[Product, ...] = (
    Product(
        "PAP-A4-80-500",
        "A4 copy paper, 80 gsm, ream of 500 sheets",
        "Copy paper A4 80g (500 sh)",
        Decimal("6.40"),
    ),
    Product(
        "PAP-LTR-20-5K",
        "Letter copy paper, 20 lb, case of 10 reams",
        "Multipurpose paper Letter 20lb - case/10",
        Decimal("54.90"),
    ),
    Product(
        "TNR-BLK-HY",
        "Toner cartridge, black, high yield",
        "Black toner, high-yield cartridge",
        Decimal("89.00"),
    ),
    Product(
        "TNR-CMY-SET",
        "Toner cartridge set, cyan/magenta/yellow",
        "Colour toner set C/M/Y",
        Decimal("214.00"),
    ),
    Product("INK-BLK-XL", "Ink cartridge, black, XL", "XL black ink cartridge", Decimal("32.50")),
    Product(
        "PEN-BP-BLU-50",
        "Ballpoint pens, blue, box of 50",
        "Blue ballpoint pen (box 50)",
        Decimal("11.80"),
    ),
    Product(
        "PEN-GEL-BLK-12", "Gel pens, black, pack of 12", "Gel ink pen black x12", Decimal("9.60")
    ),
    Product(
        "HLT-ASST-6",
        "Highlighters, assorted colours, pack of 6",
        "Highlighter marker asst. 6-pk",
        Decimal("5.20"),
    ),
    Product(
        "NTE-76-12",
        "Sticky notes 76x76 mm, pack of 12 pads",
        "Self-stick notes 3x3in, 12 pads",
        Decimal("8.75"),
    ),
    Product("STP-266-5K", "Staples 26/6, box of 5000", "Staples no. 26/6 (5,000)", Decimal("2.40")),
    Product(
        "STR-DESK-25",
        "Desk stapler, 25-sheet capacity",
        "Stapler, desktop, 25 sheet",
        Decimal("14.30"),
    ),
    Product(
        "PNC-2H-30", "Two-hole punch, 30-sheet capacity", "2-hole punch 30sh", Decimal("12.60")
    ),
    Product(
        "FIL-LAF-10",
        "Lever arch files, A4, pack of 10",
        "Lever arch file A4 75mm (10)",
        Decimal("28.40"),
    ),
    Product(
        "BND-RNG-25",
        "Ring binders, 2-ring, pack of 25",
        "2-ring binder, box of 25",
        Decimal("31.00"),
    ),
    Product(
        "ENV-C4-250",
        "C4 envelopes, self-seal, box of 250",
        "Envelope C4 peel & seal x250",
        Decimal("24.90"),
    ),
    Product(
        "ENV-DL-500",
        "DL envelopes, window, box of 500",
        "DL window envelopes (500)",
        Decimal("19.70"),
    ),
    Product(
        "LBL-A4-21-100",
        "Address labels, 21 per sheet, 100 sheets",
        "Labels 21/sheet, 100 sheets",
        Decimal("16.50"),
    ),
    Product(
        "WBM-ASST-4",
        "Whiteboard markers, assorted, pack of 4",
        "Dry-wipe markers asst x4",
        Decimal("4.90"),
    ),
    Product(
        "WBD-120-90",
        "Magnetic whiteboard, 120 x 90 cm",
        "Whiteboard magnetic 1200x900mm",
        Decimal("118.00"),
    ),
    Product(
        "MSE-ERG-WL",
        "Ergonomic wireless mouse",
        "Wireless mouse, ergonomic vertical",
        Decimal("34.00"),
    ),
    Product(
        "KBD-WL-FS", "Wireless keyboard, full size", "Full-size wireless keyboard", Decimal("42.00")
    ),
    Product("HUB-USBC-7", "USB-C hub, 7-port", "7-in-1 USB-C hub", Decimal("39.90")),
    Product("CBL-HDMI-2M", "HDMI cable, 2 m", "HDMI lead 2.0m", Decimal("7.80")),
    Product("CBL-CAT6-3M", "Cat6 patch cable, 3 m", "Patch cord Cat6 3m", Decimal("4.10")),
    Product("MON-24-FHD", "24-inch monitor, 1920x1080", '24" FHD monitor', Decimal("149.00")),
    Product(
        "ARM-MON-DUAL",
        "Dual monitor arm, desk clamp",
        "Monitor arm, dual, clamp mount",
        Decimal("96.00"),
    ),
    Product(
        "STD-LAP-ALU",
        "Laptop stand, aluminium, adjustable",
        "Adjustable laptop riser (alu)",
        Decimal("37.50"),
    ),
    Product("USB-64G", "USB flash drive, 64 GB", "64GB USB 3.2 flash drive", Decimal("9.90")),
    Product("SSD-EXT-1T", "External SSD, 1 TB, USB-C", "Portable SSD 1TB USB-C", Decimal("89.00")),
    Product("CAM-1080", "Webcam, 1080p, with privacy shutter", "HD webcam 1080p", Decimal("54.00")),
    Product(
        "HDS-USB-ST",
        "USB headset, stereo, noise-cancelling mic",
        "Stereo USB headset w/ NC mic",
        Decimal("46.00"),
    ),
    Product(
        "PWR-6W-SURGE",
        "Power strip, 6-way, surge protected",
        "6-gang surge protector",
        Decimal("21.40"),
    ),
    Product(
        "SHR-CC-P4",
        "Cross-cut shredder, P-4, 12 sheet",
        "Shredder cross cut P4 12sh",
        Decimal("165.00"),
    ),
    Product("LAM-A3", "Laminator, A3", "A3 laminating machine", Decimal("72.00")),
    Product(
        "LAM-POU-A4-100",
        "Laminating pouches, A4, 100 pack",
        "Laminator pouch A4 125mic x100",
        Decimal("13.20"),
    ),
    Product(
        "NSW-8G",
        "Network switch, 8-port gigabit, unmanaged",
        "8-port Gigabit switch",
        Decimal("29.00"),
    ),
    Product(
        "WAP-AX",
        "Wireless access point, Wi-Fi 6, ceiling mount",
        "Wi-Fi 6 access point (ceiling)",
        Decimal("139.00"),
    ),
    Product(
        "BAT-AA-24",
        "Alkaline batteries AA, pack of 24",
        "AA alkaline battery 24-pk",
        Decimal("12.90"),
    ),
    Product(
        "CHR-TASK-MESH",
        "Task chair, mesh back, adjustable arms",
        "Mesh task chair w/ adj. arms",
        Decimal("189.00"),
    ),
    Product(
        "CAB-FIL-3D",
        "Filing cabinet, 3 drawer, lockable",
        "Lockable 3-drawer filing cabinet",
        Decimal("245.00"),
    ),
)

PAYMENT_TERMS: tuple[str, ...] = ("Net 15", "Net 30", "Net 30", "Net 45", "Net 60", "2/10 Net 30")
