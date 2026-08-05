import { CheckCircle2, MapPinned, TriangleAlert } from "lucide-react";
import type { GeometryValidation } from "@/lib/schemas/analyses";

export function GeometryValidationPanel({ data }: { data: GeometryValidation }) {
  return (
    <section className="validation-strip" aria-live="polite">
      <div className="validation-title"><CheckCircle2 size={18} /><strong>Area validada</strong><span>{data.geometry_type}</span></div>
      <dl>
        <div><dt>Area</dt><dd>{data.area_square_meters.toLocaleString("pt-BR", { maximumFractionDigits: 0 })} m2</dd></div>
        <div><dt>Centroide</dt><dd>{data.centroid.latitude.toFixed(5)}, {data.centroid.longitude.toFixed(5)}</dd></div>
        <div><dt>Pixels estimados</dt><dd>{data.estimated_sentinel_pixels}</dd></div>
        <div><dt>Bounding box</dt><dd>{data.bounding_box.map((value) => value.toFixed(4)).join(", ")}</dd></div>
      </dl>
      {data.warnings.map((warning) => <p className="validation-warning" key={warning}><TriangleAlert size={15} />{warning}</p>)}
      <MapPinned className="validation-icon" aria-hidden="true" />
    </section>
  );
}
