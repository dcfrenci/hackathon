export const GET = async ({ request }) => {
  // Mock data representing backend processing (e.g. from an ML model or IoT sensor)
  const data = {
    patientId: "ANON-7842",
    status: "TRACKING ACTIVE",
    vitals: "STABLE",
    offsets: {
      ap: "+0.04",
      si: "-0.12",
      lr: "+0.00"
    },
    timestamp: new Date().toISOString()
  };

  return new Response(JSON.stringify(data), {
    status: 200,
    headers: {
      "Content-Type": "application/json"
    }
  });
}
