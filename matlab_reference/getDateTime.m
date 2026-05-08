function dateTime = getDateTime()

try
    dateTime_now = now; %#ok<TNOW1>
    dateTime_conv = datetime(dateTime_now,'ConvertFrom','datenum');
catch
    dateTime_conv = datetime('now');
end
dateTime_fix = datetime(dateTime_conv,'Format','yyyy-MM-dd h:mm:ss a');
dateTime = char(dateTime_fix);

end