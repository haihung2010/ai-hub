"""
IHI AIHub parallel listener — runs alongside PDM, reads InfluxDB, sends to AIHub.

Chạy: python scripts/ihi_aihub_listener.py
Không ảnh hưởng hệ thống hiện tại. Chỉ ghi kết quả vào AIHub DB.
"""
import asyncio
import os
import json
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

import httpx
import pandas as pd
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

# Load PDM .env which has both AIHub AND InfluxDB settings
PDM_ENV = "/home/hung/hoang-project/project-ihi/AI/pdm_optimization/.env"
load_dotenv(PDM_ENV)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

LISTENING_INTERVAL = int(os.getenv("LISTENING_INTERVAL", "30"))
AIHUB_URL = os.getenv("AIHUB_URL", "http://localhost:8000")
AIHUB_API_KEY = os.getenv("AIHUB_API_KEY", os.getenv("API_KEY", ""))

INFLUX_HOST = os.getenv("INFLUXDB_HOST", "10.254.1.79")
INFLUX_PORT = os.getenv("INFLUXDB_PORT", "8086")
INFLUX_USE_SSL = os.getenv("INFLUXDB_USE_SSL", "True").lower() == "true"
INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUXDB_ORG", "IHI")
INFLUX_BUCKET = os.getenv("INFLUXDB_BUCKET", "sensor_data")


async def fetch_sensor_data(start: str, stop: str) -> pd.DataFrame | None:
    """Fetch sensor data from InfluxDB."""
    protocol = "https" if INFLUX_USE_SSL else "http"
    url = f"{protocol}://{INFLUX_HOST}:{INFLUX_PORT}"

    try:
        async with InfluxDBClientAsync(url=url, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
            query = f'''
 from(bucket: "{INFLUX_BUCKET}")
              |> range(start: {start}, stop: {stop})
              |> pivot(rowKey:["_time"], columnKey: ["_field"], valueCell: ["_value"])
            '''
            df = await client.query_api().query_data_frame(query=query, org=INFLUX_ORG)
            if df is None or df.empty:
                return None
            df["datetime"] = pd.to_datetime(df["_time"])
            df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
            df.drop(["_time"], axis=1, inplace=True)
            return df
    except Exception as e:
        logger.error(f"InfluxDB query error: {e}")
        return None


async def send_to_aihub_analyze(sensors: list[dict]) -> dict | None:
    """Send sensor data to AIHub /v1/ihi/analyze. Returns result dict or None."""
    if not sensors:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{AIHUB_URL}/v1/ihi/analyze",
                headers={"X-API-KEY": AIHUB_API_KEY},
                json={
                    "ts": datetime.now().strftime("%d/%m %H:%M"),
                    "data": sensors
                }
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"AIHub /analyze error: {e}")
        return None


async def run_listener():
    """Main loop: read InfluxDB → send to AIHub → log result."""
    logger.info(f"Starting IHI AIHub listener (parallel to PDM)")
    logger.info(f"  InfluxDB: {INFLUX_HOST}:{INFLUX_PORT}/{INFLUX_BUCKET}")
    logger.info(f"  AIHub:    {AIHUB_URL}")
    logger.info(f"  Interval: {LISTENING_INTERVAL}s")

    while True:
        try:
            stop = datetime.utcnow().isoformat()
            start = (
                datetime.fromisoformat(stop) - timedelta(seconds=LISTENING_INTERVAL - 1)
            ).isoformat()

            df = await fetch_sensor_data(start=start, stop=stop)

            if df is None or df.empty:
                logger.info("No data from InfluxDB, waiting...")
                await asyncio.sleep(LISTENING_INTERVAL)
                continue

            # Group by device_id
            for device_id in df["device_id"].unique():
                temp_df = df[df["device_id"] == device_id].copy()
                temp_df.dropna(axis=1, how="all", inplace=True)
                records = temp_df.to_dict(orient="records")

                # Map to IHI format: id, t, v, c
                sensors = []
                for r in records:
                    sensors.append({
                        "id": device_id,
                        "t": r.get("temperature_c", r.get("temperature")),
                        "v": r.get("vibration_mm_s", r.get("vibration")),
                        "c": r.get("current_a", r.get("current")),
                    })

                # Remove None values
                sensors = [{k: v for k, v in s.items() if v is not None} for s in sensors]

                if not sensors:
                    continue

                result = await send_to_aihub_analyze(sensors)
                if result:
                    alert = result.get("alert", "UNKNOWN")
                    devices = result.get("devices", [])
                    case_id = result.get("case_id")
                    confidence = result.get("confidence", 0)
                    symptom = result.get("symptom", "")

                    logger.info(
                        f"[{device_id}] alert={alert} | "
                        f"devices={devices} | case_id={case_id} | "
                        f"conf={confidence:.2f} | symptom={symptom}"
                    )
                else:
                    logger.warning(f"[{device_id}] no result from AIHub")

            await asyncio.sleep(LISTENING_INTERVAL)

        except asyncio.CancelledError:
            logger.info("Listener stopped by user")
            break
        except Exception as e:
            logger.exception(f"Error in listener loop: {e}")
            await asyncio.sleep(LISTENING_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(run_listener())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
