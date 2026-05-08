function status = getStatus(File,channelNum)
%% Variables
Data = File.Data(channelNum);
status = '';

%% Function
isGood = Data.Status.Good;
if isGood
    status = 'Good';
else
    isAtCurrentCompliance = Data.Status.CurrentCompliance;
    if isAtCurrentCompliance
        status = ' compliance reached';
    else
        isTooLong = Data.Status.TooLong;
        if isTooLong
            status = 'Took too long';
        else
            isLimitReached = Data.Status.PotentialLimit;
            if any(isLimitReached)
                switch isLimitReached
                    case -1
                        status = 'Cathodic potential reached';
                    case 1
                        status = 'Anodic potential reached';
                end
            end
            isVoltageSafe = Data.Status.VoltageSafety;
            if ~isVoltageSafe
                status = 'Voltage is unsafe';
            end
            isAtVoltageCompliance = Data.Status.VoltageCompliance;
            if isAtVoltageCompliance
                status = 'Voltage compliance reached';
            end
        end
    end
end

end