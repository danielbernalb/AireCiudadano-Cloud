# API para lectura de aqicn y otros https://api.aireciudadano.com/fixstations
# app.py cambios por nuevo pushgateway que no devuelve JSON, 14 sept 2025

import re
import json
from datetime import datetime
import requests
from flask import Flask, jsonify, make_response

app = Flask(__name__)

def transform_data(input_json, filter_inout=False):
    output_data = []

    # Lista de tipos de medición que queremos incluir, en el orden deseado
    desired_measurements = ["PM25", "Temperature", "Humidity", "CO2"]

    for entry in input_json.get("data", []):
        # Filtra los datos por "InOut" si filter_inout es True
        if filter_inout:
            inout = entry.get("InOut")
            if inout and inout.get("metrics") and inout["metrics"][0].get("value") == "1":
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
                station["id"] = value.get("job", "")
                station["station_name"] = value.get("job", "")
            elif key == "Latitude":
                try:
                    station["decimalLatitude"] = float(value["metrics"][0]["value"])
                except Exception:
                    station["decimalLatitude"] = None
            elif key == "Longitude":
                try:
                    station["decimalLongitude"] = float(value["metrics"][0]["value"])
                except Exception:
                    station["decimalLongitude"] = None
            elif key in desired_measurements:
                # Protección por si falta metrics
                val = ""
                try:
                    val = value["metrics"][0]["value"]
                except Exception:
                    val = ""
                # Construir measurement
                measurement = {
                    "measurementID": value.get("time_stamp", ""),
                    "measurementType": key,
                    "measurementUnit": get_measurement_unit(key),
                    "measurementDeterminedDate": value.get("time_stamp", ""),
                    "measurementDeterminedBy": "",  # Se completará más abajo
                    "measurementValue": None
                }
                # Convertir a número si es posible
                try:
                    fv = float(val)
                    if fv.is_integer():
                        measurement["measurementValue"] = int(fv)
                    else:
                        measurement["measurementValue"] = fv
                except Exception:
                    measurement["measurementValue"] = val

                measurements.append(measurement)

                # Guardar timestamps (si aún vacíos)
                if not station["locationID"]:
                    station["locationID"] = value.get("time_stamp", "")
                if not station["observedOn"]:
                    station["observedOn"] = value.get("time_stamp", "")
                if not station["georeferencedDate"]:
                    station["georeferencedDate"] = value.get("time_stamp", "")

        # Si no se estableció timestamp desde mediciones, intenta usar push_time u hora actual
        if not station["locationID"]:
            # buscar push_time_seconds en entry si existe como métrica (por seguridad)
            pts = None
            push = entry.get("push_time_seconds")
            if push and push.get("metrics"):
                try:
                    pts = float(push["metrics"][0].get("value", None))
                    station_ts = datetime.utcfromtimestamp(pts).isoformat() + "Z"
                except Exception:
                    station_ts = datetime.utcnow().isoformat() + "Z"
            else:
                station_ts = datetime.utcnow().isoformat() + "Z"
            station["locationID"] = station_ts
            station["observedOn"] = station_ts
            station["georeferencedDate"] = station_ts

        # Ordena las mediciones según el orden deseado
        # Primero convertir PM25 a PM2.5 solo para presentación final, pero para ordenar usamos "PM25"
        measurements = sorted(measurements, key=lambda m: desired_measurements.index(m["measurementType"]))

        # Asegurar que siempre exista CO2 (si falta, crear con valor 0)
        has_co2 = any(m["measurementType"] == "CO2" for m in measurements)
        if not has_co2:
            co2_measurement = {
                "measurementID": station["locationID"],
                "measurementType": "CO2",
                "measurementUnit": get_measurement_unit("CO2"),
                "measurementDeterminedDate": station["locationID"],
                "measurementDeterminedBy": f"AireCiudadano station {station['station_name']}",
                "measurementValue": 0
            }
            measurements.append(co2_measurement)
            # volver a ordenar para que CO2 quede en la posición correcta
            measurements = sorted(measurements, key=lambda m: desired_measurements.index(m["measurementType"]))

        # Cambia "PM25" a "PM2.5" en la salida final y completa measurementDeterminedBy
        for measurement in measurements:
            if measurement["measurementType"] == "PM25":
                measurement["measurementType"] = "PM2.5"
            # Rellenar measurementDeterminedBy si no existe
            if not measurement.get("measurementDeterminedBy"):
                measurement["measurementDeterminedBy"] = f"AireCiudadano station {station['station_name']}"

        # Solo añadimos la estación si tiene mediciones en desired_measurements (ahora siempre tendrá CO2)
        station["measurements"] = measurements
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

def parse_prometheus_text(text):
    """
    Parsea texto en formato Prometheus (metric{labels} value)
    y devuelve una estructura similar a la que esperaba transform_data:
    {"status":"success","data":[ {...}, {...} ]}
    """
    job_metrics = {}
    push_times = {}

    # regex para líneas tipo: MetricName{key="val",key2="val2"} <value>
    pattern = re.compile(r'^([A-Za-z0-9_]+)\{([^}]*)\}\s+(.+)$')

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = pattern.match(line)
        if not m:
            # ignorar líneas no coincidentes (por ejemplo, métricas sin labels)
            continue
        metric_name, labels_str, value_str = m.groups()

        # parsear labels_str en dict
        labels = dict(re.findall(r'(\w+)\s*=\s*"(.*?)"', labels_str))
        job = labels.get("job") or labels.get("instance") or "unknown"
        instance = labels.get("instance", "")

        # limpiar el value (puede incluir timestamps si es pushgateway con timestamp, ignoramos timestamp extra)
        value = value_str.split()[0].strip()

        # almacenar push_time_seconds si aparece
        if metric_name == "push_time_seconds":
            try:
                push_times[job] = float(value)
            except Exception:
                pass

        job_entry = job_metrics.setdefault(job, {})
        job_entry.setdefault("metrics", {})
        job_entry["metrics"][metric_name] = {"instance": instance, "value": value}

    # construir lista 'data' con la estructura esperada
    data = []
    for job, info in job_metrics.items():
        entry = {}
        # determinar timestamp ISO de preferencia desde push_time_seconds
        if job in push_times:
            try:
                ts_iso = datetime.utcfromtimestamp(push_times[job]).isoformat() + "Z"
            except Exception:
                ts_iso = datetime.utcnow().isoformat() + "Z"
        else:
            ts_iso = datetime.utcnow().isoformat() + "Z"

        for metric_name, m in info.get("metrics", {}).items():
            entry[metric_name] = {
                "time_stamp": ts_iso,
                "type": "UNTYPED",
                "metrics": [{"labels": {"instance": m["instance"], "job": job}, "value": str(m["value"])}]
            }
        entry["labels"] = {"job": job}
        data.append(entry)

    return {"status": "success", "data": data}

def fetch_input_json(url):
    """
    Intenta obtener JSON; si la respuesta no es JSON intenta parsear texto Prometheus.
    Devuelve diccionario con la forma {"status":"success","data":[...]}
    """
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()

    # Intentar JSON primero
    try:
        return resp.json()
    except Exception:
        # no es JSON -> parsear como texto Prometheus
        text = resp.text
        return parse_prometheus_text(text)

@app.route('/fixstationall', methods=['GET'])
def fixstationall():
    # Define la URL del JSON/metrics de entrada
    url = "http://sensor.aireciudadano.com:30991/api/v1/metrics"
    input_json = fetch_input_json(url)
    output_json = transform_data(input_json, filter_inout=False)
    return make_response(jsonify(output_json), 200)

@app.route('/fixstations', methods=['GET'])
def fixstationall_inout():
    url = "http://sensor.aireciudadano.com:30991/api/v1/metrics"
    input_json = fetch_input_json(url)
    output_json = transform_data(input_json, filter_inout=True)
    return make_response(jsonify(output_json), 200)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
