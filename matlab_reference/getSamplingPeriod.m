function samplingPeriod = getSamplingPeriod(File)
%% Variables
isStruct = isstruct(File);
isVISA = strcmpi(class(File),'visa');
if isStruct
    scope = File.Oscilloscope.Object;      % oscilloscope
elseif isVISA
    scope = File;
end

fprintf(scope,'WFMPre:XINcr?');        % query horitzontal sampling interval
samplingPeriod_raw = fgetl(scope); %#ok<*ST2NM> 
samplingPeriod_num = str2double(samplingPeriod_raw);
samplingPeriod = abs(samplingPeriod_num);

end