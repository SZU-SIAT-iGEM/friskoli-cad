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
