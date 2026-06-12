interface UnitDefinition {
  baseUnit: string;
  toBase: number;
  fromBase: number;
}

interface UnitConversions {
  [key: string]: {
    [key: string]: UnitDefinition;
  };
}

const UNIT_CONVERSIONS: UnitConversions = {
  weight: {
    kg: { baseUnit: 'kg', toBase: 1.0, fromBase: 1.0 },
    lbs: { baseUnit: 'kg', toBase: 0.453592, fromBase: 2.20462 },
    st: { baseUnit: 'kg', toBase: 6.35029, fromBase: 0.157473 },
  },
  height: {
    cm: { baseUnit: 'cm', toBase: 1.0, fromBase: 1.0 },
    m: { baseUnit: 'cm', toBase: 100.0, fromBase: 0.01 },
    in: { baseUnit: 'cm', toBase: 2.54, fromBase: 0.393701 },
    ft: { baseUnit: 'cm', toBase: 30.48, fromBase: 0.0328084 },
  },
  glucose: {
    'mmol/L': { baseUnit: 'mmol/L', toBase: 1.0, fromBase: 1.0 },
    'mg/dL': { baseUnit: 'mmol/L', toBase: 0.05551, fromBase: 18.0182 },
  },
  bloodPressure: {
    mmHg: { baseUnit: 'mmHg', toBase: 1.0, fromBase: 1.0 },
  },
};

export class UnitConverter {
  convert(value: number, fromUnit: string, toUnit: string, category: string): number {
    if (fromUnit === toUnit) {
      return value;
    }

    const conversions = UNIT_CONVERSIONS[category];
    if (!conversions) {
      throw new Error(`Unknown unit category: ${category}`);
    }

    const fromDef = conversions[fromUnit];
    const toDef = conversions[toUnit];

    if (!fromDef || !toDef) {
      throw new Error(`Unsupported units: ${fromUnit}, ${toUnit}`);
    }

    const baseValue = value * fromDef.toBase;
    return baseValue * toDef.fromBase;
  }

  convertToUserUnit(
    value: number,
    fromUnit: string,
    userUnit: string,
    category: string
  ): number {
    return this.convert(value, fromUnit, userUnit, category);
  }

  convertToBaseUnit(value: number, fromUnit: string, category: string): number {
    const conversions = UNIT_CONVERSIONS[category];
    if (!conversions) {
      throw new Error(`Unknown unit category: ${category}`);
    }

    const unitDef = conversions[fromUnit];
    if (!unitDef) {
      throw new Error(`Unsupported unit: ${fromUnit}`);
    }

    return value * unitDef.toBase;
  }
}

export const unitConverter = new UnitConverter();