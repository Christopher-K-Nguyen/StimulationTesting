function trigSource = getTriggerSource(File)
%% Constants
SOURCE_LIST = {'EXT','CH1','CH2','CH3','CH4'};

%% Variables
try
    scope = File.Oscilloscope.Object;
catch
    scope = File;
end
numOfSources =  length(SOURCE_LIST);
trigSource = '';

%% Function
fprintf(scope,'TRIGGER:MAIN:EDGE:SOURCE?');
trigSource_check = fscanf(scope);

for sourceNum= 1:numOfSources
    source = SOURCE_LIST{sourceNum};
    if contains(trigSource_check,source,'IgnoreCase',true)
        trigSource = source;
        break;
    end
end

end