type Props = { value: string; onChange: (value: string) => void; error?: string };

export function GeometryEditor({ value, onChange, error }: Props) {
  return (
    <div className="field full-field">
      <label htmlFor="geometry">Area de interesse em GeoJSON</label>
      <textarea
        id="geometry"
        aria-label="GeoJSON da area de interesse"
        aria-invalid={Boolean(error)}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
        placeholder='{"type":"Polygon","coordinates":[...]}'
      />
      {error && <p className="field-error" role="alert">{error}</p>}
    </div>
  );
}
