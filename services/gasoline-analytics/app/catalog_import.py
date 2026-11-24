"""Explicit station-directory importer; requires a licensed or user-approved CSV."""
import argparse
import csv
import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database import GasolineDatabase
from app.models import FUEL_TYPES, to_iso, utc_now


class StationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    station_id: str = Field(min_length=1,max_length=128)
    name: str = Field(min_length=1,max_length=200)
    address: str = Field(min_length=1,max_length=300)
    latitude: float = Field(ge=-90,le=90,allow_inf_nan=False)
    longitude: float = Field(ge=-180,le=180,allow_inf_nan=False)
    available_fuel_types: list[str] = Field(min_length=1)

    @field_validator('available_fuel_types')
    @classmethod
    def validate_fuels(cls, value):
        if len(set(value)) != len(value) or any(fuel not in FUEL_TYPES for fuel in value):
            raise ValueError('Invalid or duplicate catalogued fuel type')
        return value


def import_csv(db, file, source):
    """Validate the entire CSV before a single catalog row is written."""
    stations = []
    ids = set()
    with Path(file).open(encoding='utf-8-sig',newline='') as stream:
        reader = csv.DictReader(stream)
        expected = {'station_id','name','address','latitude','longitude','available_fuel_types'}
        if set(reader.fieldnames or []) != expected:
            raise ValueError(f'Expected exactly these CSV columns: {sorted(expected)}')
        for line,row in enumerate(reader,start=2):
            try:
                data = dict(row)
                data['available_fuel_types'] = [value.strip() for value in data['available_fuel_types'].split('|')]
                station = StationInput.model_validate(data).model_dump()
                if station['station_id'] in ids:
                    raise ValueError('Repeated station_id')
                ids.add(station['station_id'])
                stations.append(station)
            except Exception as exc:
                raise ValueError(f'CSV line {line}: {exc}') from exc
    if not stations:
        raise ValueError('Refusing an empty directory import')
    db.import_stations(stations,source=source,updated_at=to_iso(utc_now()))
    return len(stations)


def main():
    parser = argparse.ArgumentParser(description='Import an S7-owned station directory (no fuel prices)')
    parser.add_argument('csv_file',help='CSV file within the container')
    parser.add_argument('--source',required=True,help='Approved station directory name/provenance')
    args = parser.parse_args()
    db = GasolineDatabase(os.getenv('S7_DB_PATH','/app/data/gasoline.sqlite3'))
    print(f'Imported {import_csv(db,args.csv_file,args.source)} stations from {args.source}')


if __name__ == '__main__':
    main()
