# p1claudef: Parece todo bien, seguir probando. 4 meses van bien.

from flask import Flask, request, jsonify, render_template_string, send_file, Response
import threading  # Import the threading module
import requests
import pandas as pd
import datetime
import numpy as np
import time
import io
import zipfile
import json
import logging

# Constants
selected_cols = [
    "PM25", "PM25raw", "PM1", "Humidity", "Temperature",
]

# Initialize Flask app and set logging level
app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

# Global lock for one-at-a-time processing
processing_lock = threading.Lock()

def create_zip_file(data, file_format='json'):
    memory_file = io.BytesIO()

    try:
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            if file_format == 'json':
                # Convert data to JSON string
                data_str = json.dumps(data, indent=2)
                zf.writestr('data.json', data_str)
            else:  # csv
                # Convert nested dict to flat dataframe
                rows = []
                for station, records in data['data'].items():
                    for record in records:
                        record['station'] = station
                        rows.append(record)
                df = pd.DataFrame(rows)

                # Convert to CSV string
                csv_buffer = io.StringIO()
                df.to_csv(csv_buffer, index=False)
                zf.writestr('data.csv', csv_buffer.getvalue())

        # Important: seek to beginning of file
        memory_file.seek(0)
        return memory_file

    except Exception as e:
        app.logger.error(f'Error creating zip file: {str(e)}')
        raise

def process_data_in_chunks(url, variables, start_datetime, end_datetime):
    chunk_size = datetime.timedelta(days=7)
    current_start = start_datetime
    all_data = []

    while current_start < end_datetime:
        current_end = min(current_start + chunk_size, end_datetime)
        # Usar step de 1 minuto
        chunk_data = get_data(url, variables, current_start, current_end, '1m', interval_minutes=60)

        # Aplicar promedio horario al chunk
#        chunk_data['date'] = pd.to_datetime(chunk_data['date'], utc=True) - pd.Timedelta(hours=1)
        chunk_data['date'] = pd.to_datetime(chunk_data['date'], utc=True)
        chunk_data.set_index(['station', 'date'], inplace=True)
        chunk_data = chunk_data.apply(pd.to_numeric, errors='coerce')

        hourly_chunks = []
        for station, group in chunk_data.groupby('station'):
            # Resamplear a intervalos horarios y calcular promedio
            hourly_data = group.resample('1h', level='date').mean()
            hourly_data.index = hourly_data.index + pd.Timedelta(hours=1)
            hourly_data['station'] = station
            hourly_chunks.append(hourly_data.reset_index())

        chunk_data = pd.concat(hourly_chunks, ignore_index=True)
        all_data.append(chunk_data)

        current_start = current_end

        # Yield progress information
        progress = {
            'current_date': current_end.isoformat(),
            'progress_percentage': min(100, (current_end - start_datetime) / (end_datetime - start_datetime) * 100)
        }
        yield progress, chunk_data

    return all_data

# Get data from API with time intervals
def get_data(url, selected_cols, start_datetime, end_datetime, step, interval_minutes=60):
    all_results = []
    current_start_time = start_datetime

    while current_start_time < end_datetime:
        # Agregar una pausa de 500 milisegundos entre consultas
        time.sleep(2)
        current_end_time = min(current_start_time + datetime.timedelta(minutes=interval_minutes), end_datetime)
        current_start_time_1s = current_start_time + pd.Timedelta(seconds=1)
        query_url = f"{url}&start={current_start_time_1s.isoformat()}Z&end={current_end_time.isoformat()}Z&step={step}"

        app.logger.debug(f"Querying data from {current_start_time_1s} to {current_end_time}")

        try:
            response = requests.get(query_url)
            response.raise_for_status()
            data = response.json().get('data', {}).get('result', [])
            time.sleep(0.5)

            # Si no hay datos en este intervalo, avanzar al siguiente
            if not data:
                app.logger.warning(f"No data returned from API for interval {current_start_time} to {current_end_time}")
                current_start_time = current_end_time
                continue

            df = pd.json_normalize(data)
#            app.logger.debug(f"Dataframe shape after json_normalize: {df.shape}")

            # Verificar si tenemos las columnas necesarias antes de procesar
            required_columns = ['metric.__name__', 'metric.exported_job', 'values'] if 'values' in df.columns else ['metric.__name__', 'metric.exported_job', 'value']
            if not all(col in df.columns for col in required_columns):
                app.logger.warning(f"Missing required columns for interval {current_start_time} to {current_end_time}")
                current_start_time = current_end_time
                continue

            # Explode values and check for presence of station column
            if 'values' in df.columns:
                df = df.explode('values')
                df['date'] = df['values'].apply(lambda x: datetime.datetime.fromtimestamp(x[0], tz=datetime.UTC).isoformat())
                df['value'] = df['values'].apply(lambda x: x[1])
                df = df.drop(columns="values")
            elif 'value' in df.columns:
                df['date'] = df['value'].apply(lambda x: datetime.datetime.utcfromtimestamp(x[0]).isoformat())
                df['value'] = df['value'].apply(lambda x: x[1])

            # Rename columns
            df = df.rename(columns={"metric.__name__": "metric_name", "metric.exported_job": "station"})
#            app.logger.debug(f"Columns in dataframe after renaming: {df.columns}")

            # Verificar si tenemos la columna station después del renombrado
            if 'station' not in df.columns:
                app.logger.warning(f"Missing 'station' column after renaming for interval {current_start_time} to {current_end_time}")
                current_start_time = current_end_time
                continue

            # Filter out null stations
            df = df[df['station'].notnull()]

            # Si no quedan filas después del filtrado, avanzar al siguiente intervalo
            if df.empty:
                app.logger.warning(f"No valid data after filtering for interval {current_start_time} to {current_end_time}")
                current_start_time = current_end_time
                continue

            try:
                df_result = _wide_table(df, selected_cols)
            except Exception as pivot_error:
                app.logger.warning(f"Error in pivot operation: {str(pivot_error)} for interval {current_start_time} to {current_end_time}")
                current_start_time = current_end_time
                continue

            # Convert numeric columns
            for col in selected_cols:
                if col in df_result.columns:
                    df_result[col] = df_result[col].astype(float)
            if 'Latitude' in df_result.columns:
                df_result['Latitude'].replace(0, np.nan, inplace=True)
            if 'Longitude' in df_result.columns:
                df_result['Longitude'].replace(0, np.nan, inplace=True)

#            app.logger.debug(f"Dataframe shape after processing and cleaning: {df_result.shape}")
            all_results.append(df_result)

        except requests.exceptions.RequestException as e:
            app.logger.error(f'Network error fetching data chunk: {str(e)}')
            raise
        except Exception as e:
            app.logger.error(f'Error processing data chunk: {str(e)}')
            # En caso de error, avanzar al siguiente intervalo en lugar de hacer raise
            current_start_time = current_end_time
            continue

        current_start_time = current_end_time

    # Si no tenemos resultados, devolver un DataFrame vacío con las columnas correctas
    if not all_results:
        app.logger.warning("No valid data found for entire time range")
        columns = ['station', 'date'] + selected_cols
        return pd.DataFrame(columns=columns)

    final_df = pd.concat(all_results, ignore_index=True)
    app.logger.debug(f"Final dataframe shape after concatenation: {final_df.shape}")
    return final_df

# Function to get wide table
def _wide_table(df, selected_cols):
    try:
        df_result = pd.pivot(df, index=['station', 'date'], columns='metric_name', values='value').reset_index()
        all_cols = ['station', 'date'] + selected_cols
        missing_cols = set(all_cols) - set(df_result.columns)
        for col in missing_cols:
            df_result[col] = np.nan
        df_result = df_result[all_cols].reset_index(drop=True)
        df_result.columns.name = ""
        return df_result
    except Exception as e:
        app.logger.error(f'Pivot Error: {str(e)}')
        raise


@app.route('/getdata')
def index():
    variables = request.args.getlist('variables') or selected_cols
    start_date = request.args.get('start_date', '2024-05-09')
    start_time = request.args.get('start_time', '08:00')
    end_date = request.args.get('end_date', '2024-05-09')
    end_time = request.args.get('end_time', '10:00')
    station_filter = request.args.get('station_filter', '')

    return render_template_string('''
        <form action="/dataresult" method="post">
            <h2>API AIRECIUDADANO v1.0</h2>
            <h3>Instructions at: <a href="https://aireciudadano.com/apidata/" target="_blank">aireciudadano.com/apidata</a></h3><br>
            <label for="variables">Select variables:</label><br>
            <br>
            {% for col in selected_cols %}
                <input type="checkbox" id="{{ col }}" name="variables" value="{{ col }}" {% if col in variables %}checked{% endif %}>
                <label for="{{ col }}">{{ col }}</label><br>
            {% endfor %}
            <br>
            <label for="start_date">Start date/time:</label>
            <input type="date" id="start_date" name="start_date" value="{{ start_date }}">
            <label for="start_time"> / </label>
            <input type="time" id="start_time" name="start_time" value="{{ start_time }}" step="3600" list="hour-markers"><br><br>
            <label for="end_date">End date/time:</label>
            <input type="date" id="end_date" name="end_date" value="{{ end_date }}">
            <label for="end_time"> / </label>
            <input type="time" id="end_time" name="end_time" value="{{ end_time }}" step="3600" list="hour-markers"><br><br>
            <datalist id="hour-markers">
                {% for hour in range(24) %}
                    <option value="{{ '%02d:00'|format(hour) }}"></option>
                {% endfor %}
            </datalist>
            <label for="station_filter">Station Filter:</label>
            <input type="text" id="station_filter" name="station_filter" value=""><br><br>

            <label for="result_format">Result format:</label>
            <select id="result_format" name="result_format">
                <option value="screen">Result in screen</option>
                <option value="filejson">Result in json ZIP file</option>
                <option value="filecsv">Result in csv ZIP file</option>
            </select><br><br>

            <input type="submit" value="Submit">
        </form>
        <script>
            function toggle(source) {
                checkboxes = document.getElementsByName('variables');
                for (var i = 0; i < checkboxes.length; i++) {
                    checkboxes[i].checked = source.checked;
                }
            }
        </script>
    ''', selected_cols=selected_cols, variables=variables, start_date=start_date,
       start_time=start_time, end_date=end_date, end_time=end_time)

@app.route('/dataresult', methods=['POST'])
def data():
    # Attempt to acquire the lock
    if not processing_lock.acquire(blocking=False):  # Non-blocking check for lock
        # If lock is already held, return a message to the user
        return jsonify({
            'error': 'The API is currently processing another request. Please wait and try again shortly.'
        })

    # If lock is acquired, proceed with data processing
    start_time = time.time()
    try:
        variables = request.form.getlist('variables')
        base_url = "http://sensor.aireciudadano.com:30000/api/v1"
        query = '{job%3D"pushgateway"}'

        start_date = request.form['start_date']
        start_time_str = request.form['start_time']
        end_date = request.form['end_date']
        end_time = request.form['end_time']
        station_filter = request.form.get('station_filter', '')
        result_format = request.form.get('result_format', 'screen')

        start_datetime = datetime.datetime.fromisoformat(f"{start_date}T{start_time_str}") - datetime.timedelta(minutes=60)
        end_datetime = datetime.datetime.fromisoformat(f"{end_date}T{end_time}")
        date_diff = end_datetime - start_datetime + datetime.timedelta(minutes=60)

        if date_diff.days > 2:
            processing_lock.release()
            return jsonify({
                'error': 'El rango de fechas es demasiado largo. Segmenta y/o reduce el rango para evitar problemas de bloqueos por RAM y CPU del servidor o escribenos para enviarte el archivo python del API para que lo ejecutes en local y no tengas problemas'
            })

        # Process data
        if date_diff.days > 1:
            all_data = []
            for progress, chunk_data in process_data_in_chunks(f"{base_url}/query_range?query={query}", variables, start_datetime, end_datetime):
                if station_filter:
                    filters = station_filter.split(',')
                    chunk_data = chunk_data[chunk_data['station'].str.contains('|'.join(filters), case=False)]
                all_data.append(chunk_data)
            obs = pd.concat(all_data, ignore_index=True)
        else:
            # Obtener datos y redondear a 3 decimales todas las columnas numéricas
            obs = get_data(f"{base_url}/query_range?query={query}", variables, start_datetime, end_datetime, '1m')

            # Apply hourly average
            obs['date'] = pd.to_datetime(obs['date'], utc=True)
            obs.set_index(['station', 'date'], inplace=True)
            obs = obs.apply(pd.to_numeric, errors='coerce')

            hourly_obs = []
            for station, group in obs.groupby('station'):
                hourly_data = group.resample('1h', level='date').mean()
                hourly_data.index = hourly_data.index + pd.Timedelta(hours=1)
                hourly_data['station'] = station
                hourly_obs.append(hourly_data.reset_index())

            obs = pd.concat(hourly_obs, ignore_index=True)

            if station_filter:
                filters = station_filter.split(',')
                obs = obs[obs['station'].str.contains('|'.join(filters), case=False)]

        # Convertir las fechas a UTC
        start_datetime = pd.to_datetime(start_datetime).tz_localize('UTC')
        end_datetime = pd.to_datetime(end_datetime).tz_localize('UTC')

        # Filtrar el DataFrame por el rango de fechas
        obs = obs[(obs['date'] >= start_datetime) & (obs['date'] <= end_datetime)]

        # Redondear a 3 decimales antes de guardar en JSON o CSV
        obs = obs.round(3)

        total_records = obs.shape[0]
        obs['date'] = obs['date'].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        json_data = obs.to_dict(orient='records')

        # Clean up NaN values
        for record in json_data:
            for key, value in record.items():
                if pd.isna(value):
                    record[key] = None

        # Group by station
        grouped_data = {}
        for record in json_data:
            station = record.pop('station')
            if station not in grouped_data:
                grouped_data[station] = []
            grouped_data[station].append(record)

        process_duration = time.time() - start_time
        hours, remainder = divmod(int(process_duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        formatted_duration = f"{hours}:{minutes:02}:{seconds:02}"

        result_data = {
            'total_records': total_records,
            'data': grouped_data,
            'process_duration': formatted_duration
        }

        processing_lock.release()

        if result_format == 'screen':
            app.logger.debug(f"Data result in screen")
            return jsonify(result_data)
        else:
            # Crear y enviar archivo
            try:
                memory_file = create_zip_file(result_data, 'json' if result_format == 'filejson' else 'csv')

                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f'data_{start_date}_{end_date}_{timestamp}.zip'
                app.logger.debug(f"Data result in file")
                return Response(
                    memory_file.getvalue(),
                    mimetype='application/zip',
                    headers={
                        'Content-Disposition': f'attachment; filename={filename}',
                        'Content-Type': 'application/zip'
                    }
                )

            except Exception as e:
                app.logger.error(f'Error creating download file: {str(e)}')
                return jsonify({'error': f'Error creating download file: {str(e)}'})

    except Exception as e:
        app.logger.error(f'Error in data endpoint: {str(e)}')
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8081)
