"""Shared typed ports and parameter declarations; no execution dependencies."""

def port(shape, quantity, unit, species=False):
    result = {"shape": shape, "quantity": quantity, "unit": unit}
    if species:
        result["species_parameter"] = "species"
    return result


def number(unit="1", minimum=0, maximum=None):
    result = {"type": "number", "unit": unit, "minimum": minimum}
    if maximum is not None:
        result["maximum"] = maximum
    return result


SPECIES = {"type": "string"}
AREA = port("cell.scalar", "surface_area", "um^2")
COPIES = port("cell.scalar", "carrier_copies", "molecule")
CONCENTRATION = port("cell.scalar", "concentration", "uM", True)
REQUEST = port("cell.scalar", "requested_flux", "molecule/s", True)
ACCEPTED = port("cell.scalar", "accepted_flux", "molecule/s", True)
INVENTORY = port("global.scalar", "bulk_inventory", "molecule", True)
BULK_C = port("global.scalar", "concentration", "uM", True)
SIGNAL_PARAMETERS = {
    "ei_total_um": number("uM"), "ei_dephos_per_molecule": number("1/molecule"),
    "ei_rephos_s": number("1/s"), "ei_chea_inhibition_um": number("uM"),
    "chea_total_um": number("uM"), "chey_total_um": number("uM"),
    "chey_phos_per_um_s": number("1/(uM*s)"), "chey_dephos_s": number("1/s"),
    "motor_hill": number(), "motor_half_um": number("uM"),
}


