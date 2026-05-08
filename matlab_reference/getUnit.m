function unit = getUnit(File,scopeChannel)
try
    scope = File.Oscilloscope.Object;
catch
    scope = File;
end
unit_use = sprintf('%s:YUNit?',scopeChannel);
fprintf(scope,unit_use);
unit_raw = fgetl(scope);
if contains(unit_raw,'V')
    unit = 'V';
elseif contains(unit_raw,'A')
    unit = 'A';
end

end