function setTriggerLevel(File,amplitude1)
%% Constants
currentMonScale_V_uA = 1e-3;% mV/uA to V/uA

%% Variables
% subjectSelect = File.Subject;
scope = File.Oscilloscope.Object;
scope = setScopeStatus(scope,'open');
trigSource = File.Oscilloscope.Trigger.Source;

%% Function
% Adjust trigger level
fprintf('Setting trigger Level...');
if strcmpi(trigSource,'EXT')
%     fprintf(scope,'TRIGger:MAIn:LEVel?');
%     triggerLevel_check = round(str2num(fscanf(scope)),2,'significant');
%     if triggerLevel_check == 0
        pause(1);
        triggerLevel_use = 'TRIGger:MAIn SETLevel';
        fprintf(scope,triggerLevel_use);
%         fprintf(scope,'TRIGger:MAIn:LEVel?');
%         triggerLevel = abs(str2num(fscanf(scope))); %#ok<*ST2NM> 
%         triggerLevel_use = sprintf('TRIGger:MAIn:LEVel %.2e',triggerLevel);
%         fprintf(scope,triggerLevel_use);
%         levelCheck = triggerLevel * 2 * 10;
%         if levelCheck < 2
%           scale = 0.5;
%         elseif levelCheck < 4
%           scale = 1;
%         elseif levelCheck < 8
%             scale = 2;
%         elseif levelCheck < 20
%             scale = 5;
%         end
%         scale = 2;
%         scale_use = sprintf('CH3:SCAle %.2e',scale);
%         fprintf(scope,scale_use);
%         fprintf('%.2e V\n',triggerLevel);
%     else
        fprintf('OK\n');
%     end
else
    phaseWidth = File.Parameters.PhaseWidth1;
    amplitude1_mag = abs(amplitude1);
    amplitude1_sign = sign(amplitude1);
    if phaseWidth >= 100
        %     if contains(subjectSelect,'A')
        scale = 0.4;
        %     else
        %         scale = 0.5;
        %     end
    else
        scale = 0.25;
    end

    if amplitude1_mag <= 20  % current amplitude too small
        triggerLevel = (amplitude1_mag  + 4.5) * amplitude1_sign * currentMonScale_V_uA;
    else
        triggerLevel = amplitude1 * currentMonScale_V_uA * scale;
    end
    triggerLevel_use = sprintf('TRIGger:MAIn:LEVel %0.2e',triggerLevel);
    fprintf(scope,triggerLevel_use);
    unit = getUnit(File,'CH2');
    fprintf('%.2e %s\n',triggerLevel,unit);
end


end