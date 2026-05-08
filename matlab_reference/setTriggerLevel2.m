function setTriggerLevel2(File)
%% Variables
% subjectSelect = File.Subject;
oscilloscope = File.Oscilloscope.Object;
setScopeStatus(oscilloscope,'open');

%% Function
% Adjust trigger level
fprintf('Setting trigger Level...');
startTime = tic;
triggerLevel = 0.2;
triggerLevel_check = 0;
triggerLevel_use = sprintf('TRIGger:MAIn:LEVel %.2e',triggerLevel);
%     triggerLevel_use = 'TRIGger:MAIn SETLevel';
while triggerLevel_check ~= triggerLevel
    fprintf(oscilloscope,triggerLevel_use);
    fprintf(oscilloscope,'TRIGger:MAIn:LEVel?');
    triggerLevel_raw = fgetl2(oscilloscope);
    triggerLevel_check = str2double(triggerLevel_raw);
    if toc(startTime) > 1
        break;
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end