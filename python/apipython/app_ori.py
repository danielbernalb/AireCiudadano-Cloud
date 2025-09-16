# API para lectura de aqicn y otros https://api.aireciudadano.com/fixstations

import json
import requests
from flask import Flask, jsonify, make_response

app = Flask(__name__)

def transform_data(input_json, filter_inout=False):
    output_data = []

    # Lista de tipos de medición que queremos incluir, en el orden deseado
    desired_measurements = ["PM25", "Temperature", "Humidity", "CO2"]

    for entry in input_json["data"]:
        # Filtra los datos por "InOut" si filter_inout es True
        if filter_inout:
            # Verifica el valor de "InOut"
            if "InOut" in entry and entry["InOut"]["metrics"][0]["value"] == "1":
                continue  # No procesar esta entrada si InOut es "1"

        station = {
            "id": "",
            "station_name": "",
            "scientificName": "AireCiudadano Air quality Station",
            "ownerInstitutionCodeProperty": "AireCiudadano",
            "type": "FixedStation",
            "license": "CC BY-NC-SA 3.0",
            "measurements": [],
            "locationID": "",
            "georeferencedBy": "AireCiudadano firmware",
            "georeferencedDate": "",
            "decimalLatitude": None,
            "decimalLongitude": None,
            "observedOn": ""
        }

        measurements = []

        for key, value in entry.items():
            if key == "labels":
                station["id"] = value["job"]
                station["station_name"] = value["job"]
            elif key == "Latitude":
                station["decimalLatitude"] = float(value["metrics"][0]["value"])
            elif key == "Longitude":
                station["decimalLongitude"] = float(value["metrics"][0]["value"])
            elif key in desired_measurements:
                measurement = {
                    "measurementID": value["time_stamp"],
                    "measurementType": key,
                    "measurementUnit": get_measurement_unit(key),
                    "measurementDeterminedDate": value["time_stamp"],
                    "measurementDeterminedBy": "",  # Se actualizará más adelante
                    "measurementValue": int(float(value["metrics"][0]["value"]))
                }
                measurements.append(measurement)
                station["locationID"] = value["time_stamp"]
                station["observedOn"] = value["time_stamp"]
                station["georeferencedDate"] = value["time_stamp"]

        # Ordena las mediciones según el orden deseado
        station["measurements"] = sorted(measurements, key=lambda m: desired_measurements.index(m["measurementType"]))

        # Cambia "PM25" a "PM2.5" en la salida final
        for measurement in station["measurements"]:
            if measurement["measurementType"] == "PM25":
                measurement["measurementType"] = "PM2.5"
            # Asegura que station_name se añade correctamente a measurementDeterminedBy
            measurement["measurementDeterminedBy"] = f"AireCiudadano station {station['station_name']}"

        # Solo añadimos la estación si tiene mediciones en desired_measurements
        if station["measurements"]:
            output_data.append(station)

    return output_data

def get_measurement_unit(measurement_type):
    # Define un mapeo de tipo de medición a unidad
    unit_mapping = {
        "PM25": "ug/m3",
        "Temperature": "°C",
        "Humidity": "%",
        "CO2": "ppm",
    }
    return unit_mapping.get(measurement_type, "")

@app.route('/fixstationall', methods=['GET'])
def fixstationall():
    # Define la URL del JSON de entrada
    url = "http://sensor.aireciudadano.com:30991/api/v1/metrics"
    response = requests.get(url)
    response.raise_for_status()
    input_json = response.json()
    output_json = transform_data(input_json)
    return make_response(jsonify(output_json), 200)

@app.route('/fixstations', methods=['GET'])
def fixstationall_inout():
    # Define la URL del JSON de entrada
    url = "http://sensor.aireciudadano.com:30991/api/v1/metrics"
    response = requests.get(url)
    response.raise_for_status()
    input_json = response.json()
    output_json = transform_data(input_json, filter_inout=True)
    return make_response(jsonify(output_json), 200)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
