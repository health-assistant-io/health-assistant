from typing import Any, List, Dict
import datetime
from .base import BaseWebhookParser
from app.schemas.fhir.observation import ObservationCreate
from app.integrations.sdk.observation_builder import ObservationBuilder
import logging

logger = logging.getLogger(__name__)

class LifeDashboardParser(BaseWebhookParser):
    """Parses payloads from the Life Dashboard Companion App (Health Connect & Screen Time)."""
    
    def _parse_time(self, time_str: str) -> datetime.datetime:
        """Helper to parse ISO8601 strings to datetime."""
        try:
            if time_str.endswith('Z'):
                time_str = time_str[:-1] + '+00:00'
            return datetime.datetime.fromisoformat(time_str)
        except Exception:
            return datetime.datetime.now(datetime.timezone.utc)
            
    def parse(self, payload: Any, config: Dict[str, Any], builder: ObservationBuilder) -> List[ObservationCreate]:
        observations = []
        
        if not isinstance(payload, dict):
            return []
            
        source = payload.get("source")
        
        if source == "health_connect":
            # Activity
            for step_record in (payload.get("steps") or []):
                count = step_record.get("count")
                if count is not None:
                    time_val = self._parse_time(step_record.get("end_time", ""))
                    observations.append(builder.set_biomarker("41950-7", "Number of steps") \
                        .set_value(float(count), "steps", "{steps}").set_effective_date(time_val).build())
                        
            for dist_record in (payload.get("distance") or []):
                meters = dist_record.get("meters")
                if meters is not None:
                    time_val = self._parse_time(dist_record.get("end_time", ""))
                    observations.append(builder.set_biomarker("distance", "Distance") \
                        .set_value(float(meters), "m", "m").set_effective_date(time_val).build())

            for cal_record in (payload.get("active_calories") or []):
                calories = cal_record.get("calories")
                if calories is not None:
                    time_val = self._parse_time(cal_record.get("end_time", ""))
                    observations.append(builder.set_biomarker("active-calories", "Active Calories") \
                        .set_value(float(calories), "kcal", "kcal").set_effective_date(time_val).build())

            for cal_record in (payload.get("total_calories") or []):
                calories = cal_record.get("calories")
                if calories is not None:
                    time_val = self._parse_time(cal_record.get("end_time", ""))
                    observations.append(builder.set_biomarker("total-calories", "Total Calories") \
                        .set_value(float(calories), "kcal", "kcal").set_effective_date(time_val).build())

            # Body
            for weight_record in (payload.get("weight") or []):
                kg = weight_record.get("kilograms")
                if kg is not None:
                    time_val = self._parse_time(weight_record.get("time", ""))
                    observations.append(builder.set_biomarker("29463-7", "Body weight") \
                        .set_value(float(kg), "kg", "kg").set_effective_date(time_val).build())

            for height_record in (payload.get("height") or []):
                meters = height_record.get("meters")
                if meters is not None:
                    time_val = self._parse_time(height_record.get("time", ""))
                    observations.append(builder.set_biomarker("8302-2", "Body height") \
                        .set_value(float(meters), "m", "m").set_effective_date(time_val).build())

            for temp_record in (payload.get("body_temperature") or []):
                celsius = temp_record.get("celsius")
                if celsius is not None:
                    time_val = self._parse_time(temp_record.get("time", ""))
                    observations.append(builder.set_biomarker("8310-5", "Body temperature") \
                        .set_value(float(celsius), "Cel", "Cel").set_effective_date(time_val).build())

            # Body Composition
            for fat_record in (payload.get("body_fat") or []):
                perc = fat_record.get("percentage")
                if perc is not None:
                    time_val = self._parse_time(fat_record.get("time", ""))
                    observations.append(builder.set_biomarker("41982-0", "Body fat percentage") \
                        .set_value(float(perc), "%", "%").set_effective_date(time_val).build())

            for lbm_record in (payload.get("lean_body_mass") or []):
                kg = lbm_record.get("kilograms")
                if kg is not None:
                    time_val = self._parse_time(lbm_record.get("time", ""))
                    observations.append(builder.set_biomarker("lean-body-mass", "Lean Body Mass") \
                        .set_value(float(kg), "kg", "kg").set_effective_date(time_val).build())

            for bone_record in (payload.get("bone_mass") or []):
                kg = bone_record.get("kilograms")
                if kg is not None:
                    time_val = self._parse_time(bone_record.get("time", ""))
                    observations.append(builder.set_biomarker("bone-mass", "Bone Mass") \
                        .set_value(float(kg), "kg", "kg").set_effective_date(time_val).build())

            for water_record in (payload.get("body_water_mass") or []):
                kg = water_record.get("kilograms")
                if kg is not None:
                    time_val = self._parse_time(water_record.get("time", ""))
                    observations.append(builder.set_biomarker("body-water-mass", "Body Water Mass") \
                        .set_value(float(kg), "kg", "kg").set_effective_date(time_val).build())
                        
            # Vitals
            for hr_record in (payload.get("heart_rate") or []):
                bpm = hr_record.get("bpm")
                if bpm is not None:
                    time_val = self._parse_time(hr_record.get("time", ""))
                    observations.append(builder.set_biomarker("8867-4", "Heart rate") \
                        .set_value(float(bpm), "bpm", "{beats}/min").set_effective_date(time_val).build())
                        
            for rhr_record in (payload.get("resting_heart_rate") or []):
                bpm = rhr_record.get("bpm")
                if bpm is not None:
                    time_val = self._parse_time(rhr_record.get("time", ""))
                    observations.append(builder.set_biomarker("40443-4", "Heart rate resting") \
                        .set_value(float(bpm), "bpm", "{beats}/min").set_effective_date(time_val).build())

            for hrv_record in (payload.get("heart_rate_variability") or []):
                rmssd = hrv_record.get("heart_rate_variability_millis")
                if rmssd is not None:
                    time_val = self._parse_time(hrv_record.get("time", ""))
                    observations.append(builder.set_biomarker("80404-7", "Heart rate variability (RMSSD)") \
                        .set_value(float(rmssd), "ms", "ms").set_effective_date(time_val).build())
                        
            for bp_record in (payload.get("blood_pressure") or []):
                sys = bp_record.get("systolic")
                dia = bp_record.get("diastolic")
                time_val = self._parse_time(bp_record.get("time", ""))
                if sys is not None:
                    observations.append(builder.set_biomarker("8480-6", "Systolic blood pressure") \
                        .set_value(float(sys), "mmHg", "mm[Hg]").set_effective_date(time_val).build())
                if dia is not None:
                    observations.append(builder.set_biomarker("8462-4", "Diastolic blood pressure") \
                        .set_value(float(dia), "mmHg", "mm[Hg]").set_effective_date(time_val).build())

            for bg_record in (payload.get("blood_glucose") or []):
                mmol = bg_record.get("mmol_per_liter")
                if mmol is not None:
                    time_val = self._parse_time(bg_record.get("time", ""))
                    observations.append(builder.set_biomarker("15074-8", "Glucose") \
                        .set_value(float(mmol), "mmol/L", "mmol/L").set_effective_date(time_val).build())
                        
            for spo2_record in (payload.get("oxygen_saturation") or []):
                percentage = spo2_record.get("percentage")
                if percentage is not None:
                    time_val = self._parse_time(spo2_record.get("time", ""))
                    observations.append(builder.set_biomarker("59408-5", "Oxygen saturation") \
                        .set_value(float(percentage), "%", "%").set_effective_date(time_val).build())

            for resp_record in (payload.get("respiratory_rate") or []):
                rate = resp_record.get("rate")
                if rate is not None:
                    time_val = self._parse_time(resp_record.get("time", ""))
                    observations.append(builder.set_biomarker("9279-1", "Respiratory rate") \
                        .set_value(float(rate), "breaths/min", "{breaths}/min").set_effective_date(time_val).build())

            # Sleep
            for sleep_record in (payload.get("sleep") or []):
                duration = sleep_record.get("duration_seconds")
                if duration is not None:
                    end_time = self._parse_time(sleep_record.get("session_end_time", ""))
                    hours = duration / 3600.0
                    observations.append(builder.set_biomarker("93832-4", "Sleep duration") \
                        .set_value(hours, "h", "h").set_effective_date(end_time).build())
                    
                for stage_record in (sleep_record.get("stages") or []):
                    stage_duration = stage_record.get("duration_seconds")
                    stage_name = stage_record.get("stage")
                    if stage_duration is not None and stage_name:
                        stage_end = self._parse_time(stage_record.get("end_time", ""))
                        stage_hours = stage_duration / 3600.0
                        observations.append(builder.set_biomarker(f"sleep-stage-{stage_name.lower()}", f"Sleep duration - {stage_name.capitalize()}") \
                            .set_value(stage_hours, "h", "h").set_effective_date(stage_end).build())

            # Hydration & Nutrition
            for hyd_record in (payload.get("hydration") or []):
                liters = hyd_record.get("liters")
                if liters is not None:
                    time_val = self._parse_time(hyd_record.get("end_time", ""))
                    observations.append(builder.set_biomarker("hydration", "Hydration") \
                        .set_value(float(liters), "L", "L").set_effective_date(time_val).build())

            for nut_record in (payload.get("nutrition") or []):
                time_val = self._parse_time(nut_record.get("end_time", ""))
                calories = nut_record.get("calories")
                protein = nut_record.get("protein_grams")
                carbs = nut_record.get("carbs_grams")
                fat = nut_record.get("fat_grams")
                
                if calories is not None:
                    observations.append(builder.set_biomarker("nutrition-calories", "Consumed Calories") \
                        .set_value(float(calories), "kcal", "kcal").set_effective_date(time_val).build())
                if protein is not None:
                    observations.append(builder.set_biomarker("nutrition-protein", "Consumed Protein") \
                        .set_value(float(protein), "g", "g").set_effective_date(time_val).build())
                if carbs is not None:
                    observations.append(builder.set_biomarker("nutrition-carbs", "Consumed Carbs") \
                        .set_value(float(carbs), "g", "g").set_effective_date(time_val).build())
                if fat is not None:
                    observations.append(builder.set_biomarker("nutrition-fat", "Consumed Fat") \
                        .set_value(float(fat), "g", "g").set_effective_date(time_val).build())

            # Mindfulness
            for mind_record in (payload.get("mindfulness") or []):
                duration = mind_record.get("duration_seconds")
                if duration is not None:
                    time_val = self._parse_time(mind_record.get("end_time", ""))
                    title = mind_record.get("title") or "Mindfulness"
                    mins = duration / 60.0
                    observations.append(builder.set_biomarker("mindfulness", title) \
                        .set_value(float(mins), "min", "min").set_effective_date(time_val).build())

        elif source == "screen_time":
            for st_record in (payload.get("screen_time") or []):
                mins = st_record.get("total_screen_time_minutes")
                date_str = st_record.get("date")
                if mins is not None and date_str:
                    try:
                        time_val = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
                    except Exception:
                        time_val = datetime.datetime.now(datetime.timezone.utc)
                        
                    observations.append(builder.set_biomarker("screen-time", "Total Screen Time") \
                        .set_value(float(mins), "min", "min").set_effective_date(time_val).build())
                        
        return observations
