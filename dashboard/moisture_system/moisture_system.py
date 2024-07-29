import serial
from datetime import datetime
import json
import numpy as np
from scipy.interpolate import interpn
import threading
from repository.data import save_sensor_data, save_irrigation_data, get_last_irrigation_data, get_last_sensor_data
from dotenv import dotenv_values
from time import sleep
from statistics import mean

pump_state = False

optimal_m = get_last_irrigation_data()['optimal_m']
target = get_last_irrigation_data()['optimal_m']
useMatrix = False

lock = threading.Lock()
ser = serial.Serial(dotenv_values(".env")["SERIAL_PORT"], int(dotenv_values(".env")["SERIAL_BAUDRATE"]), timeout=1)


def set_moisture(value):
    global target
    global useMatrix

    lock.acquire()
    useMatrix = False
    target = value
    lock.release()

def set_moisture_from_matrix(matrix):
    global target
    global useMatrix

    lock.acquire()
    useMatrix = True
    target = transform_to_matrix(matrix)
    lock.release()

def transform_to_matrix(data):
    matrix = {}
    for item in data:
        x = item["x"]
        y = item["y"]
        v = item["v"]
        if x not in matrix:
            matrix[x] = {}
        matrix[x][y] = v
    return matrix

def compute_difference(matrix1, matrix2):
    differences = []
    for x in matrix1:
        for y in matrix1[x]:
            if x in matrix2 and y in matrix2[x]:
                diff = matrix1[x][y] - matrix2[x][y]
                differences.append(diff)
    return differences

def calculate_average(differences):
    if differences:
        return sum(differences) / len(differences)
    else:
        return 0

def togglePump():
    global pump_state
    pump_state = not pump_state
    ser.write(b"1" if pump_state else b"0")
    return pump_state

def receive():
    x_values = [10, 30]
    y_values = [5, 15, 25]
    points = np.array([[x, y] for x in x_values for y in y_values])

    buffer = ''

    while True:
        try:
            bytes_to_read = ser.inWaiting()
            if bytes_to_read > 0:
                read = ser.read(bytes_to_read)
                read_string = read.decode('utf-8')
                if(read_string.find('\x00') != -1):
                    print("ERRORE read: "+ read + " read_string: " + read_string)
                buffer += read_string

            if '\n' in buffer:
                lines = buffer.split('\n')
                last_received = lines[-2]
                buffer = lines[-1]
                data = json.loads(last_received.strip())
                values = data["data"]
                formatted_values = []
                for point in points:
                    found = False
                    for value in values:
                        if value["x"] == point[0] and value["y"] == point[1]:
                            formatted_values.append(value["v"])
                            found = True
                            break
                    if not found:
                        formatted_values.append(0)
                values_grid = np.array(formatted_values).reshape((len(np.unique(points[:,0])), len(np.unique(points[:,1]))))
                x_range = np.arange(10, 31, 1)  # Da 10 a 30 cm ogni 1 cm
                y_range = np.arange(5, 26, 1)   # Da 5 a 25 cm ogni 1 cm
                x_grid, y_grid = np.meshgrid(x_range, y_range)
                xi = np.vstack([x_grid.ravel(), y_grid.ravel()]).T
                interpolated_values = interpn((x_values, y_values), values_grid, xi)
                new_data = []
                for i, point in enumerate(xi):
                    new_data.append({
                        "x": int(point[0]),
                        "y": int(point[1]),
                        "v": int(interpolated_values[i])
                    })
                data["data"] = new_data
                data["timestamp"] = datetime.now().timestamp()
                with lock:
                    save_sensor_data(data)
        except Exception as e:
            print("Error receiving data:", e)

def compute_irrigation():
    # Wait for the Arduino to boot
    sleep(5)
    while True:
        global r
        global old_irrigation
        global old_r
        global target

        last_irrigation_data = get_last_irrigation_data()
        old_irrigation = last_irrigation_data["irrigation"]
        old_r = last_irrigation_data["r"]
        last_sensor_data = get_last_sensor_data()["data"]
        current_moisture = mean(map(lambda x: x['v'], last_sensor_data))
        print(f"Current moisture: {current_moisture}")

        with lock:
            if useMatrix:
                last_sensor_data = get_last_sensor_data(interpolate=True)["data"]
                last_matrix = transform_to_matrix(last_sensor_data)
                differences = compute_difference(target, last_matrix)
                r = calculate_average(differences)
            else:
                r = target - current_moisture  # Inverti l'errore per ottenere un valore positivo

        kp = 0.5  # Imposta e ottimizza questi valori
        ki = 0.3  # Imposta e ottimizza questi valori

        # Calcola il nuovo valore di irrigazione
        irrigation = old_irrigation + kp * r + ki * (r - old_r)
        irrigation = min(max(0, irrigation), 15)  # Limita l'irrigazione a valori non negativi

        print(f"Calculated irrigation: {irrigation}")

        if irrigation >= 0:
            # Apri la pompa per il tempo calcolato
            threading.Thread(target=open_pump, args=(irrigation,)).start()

        if useMatrix:
            save_irrigation_data({
                "timestamp": datetime.now().timestamp(),
                "r": r,
                "irrigation": irrigation,
                "optimal_m": 0,
                "current_m": current_moisture
            })
        else:
            save_irrigation_data({
                "timestamp": datetime.now().timestamp(),
                "r": r,
                "irrigation": irrigation,
                "optimal_m": target,
                "current_m": current_moisture
            })

        sleep(15)

def open_pump(seconds):
    if seconds > 0 :
        ser.write(b"1")
        sleep(seconds)
    ser.write(b"0")