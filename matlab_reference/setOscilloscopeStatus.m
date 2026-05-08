function File = setOscilloscopeStatus(File,status)
%% Variables
% Object
numOfDevices = length(File.Oscilloscope);

% Input
toOpen = contains2(status,'open');
toClose = contains2(status,'close');

%% Function
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    model = File.Oscilloscope(deviceNum).Model;

    % Scope Status
    scopeStatus = oscilloscope.Status;
    isClose = strcmpi(scopeStatus,'closed');
    isOpen = strcmpi(scopeStatus,'open');
    if toOpen && isClose
        fprintf('Opening %s status...',model);
    elseif toClose && isOpen
        fprintf('Closing %s status...',model);
    end
    startTime = tic;

    if toOpen && isClose
        status_check = oscilloscope.Status;
        while contains(status,status_check)
            try
                fopen(oscilloscope);
            catch
                pause(0.5);
            end
        end
    elseif toClose && isOpen
        status_check = oscilloscope.Status;
        while contains(status,status_check)
            try
                fopen(oscilloscope);
            catch
                pause(0.5);
            end
        end
    end

    % Return object
    File.Oscilloscope(deviceNum).Object = oscilloscope;
    if (toOpen && isClose) || (toClose && isOpen)
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
    end
    
end

end