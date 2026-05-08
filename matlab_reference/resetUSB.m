function isQuit = resetUSB()
%% Variables
isQuit = false;

%% Function
fprintf('Resetting USB connections...')
% Disable USB devices
disableTime = tic;
disable_cmd = 'powershell "Get-WmiObject Win32_PnPEntity | Where-Object { $_.DeviceID -match ''USB\\ROOT_HUB'' } | ForEach-Object { Disable-PnpDevice -InstanceId $_.DeviceID -Confirm:$false }"';
[status,~] = system(disable_cmd);
[endDisableTime,unit] = getEndTime(disableTime);
if status == 0
    fprintf('disabled (%.2f %s)...',endDisableTime,unit);
else
    isQuit= true;
    fprintf('FAILED (%.2f %s)\n',endDisableTime,unit);
end

if status == 0
    % Pause to allow the system to apply changes
    pause(5); % Pausing for 5 seconds

    % Re-enable USB devices
    enableTime = tic;
    enable_cmd = 'powershell "Get-WmiObject Win32_PnPEntity | Where-Object { $_.DeviceID -match ''USB\\ROOT_HUB'' } | ForEach-Object { Enable-PnpDevice -InstanceId $_.DeviceID -Confirm:$false }"';
    [status,~] = system(enable_cmd);
    [endEnableTime,unit] = getEndTime(enableTime);
    if status == 0
        fprintf('disabled (%.2f %s)...',endEnableTime,unit);
    else
        isQuit= true;
        fprintf('FAILED (%.2f %s)\n',endEnableTime,unit);
    end
end

end